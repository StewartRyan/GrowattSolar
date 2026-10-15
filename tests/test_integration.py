"""Configuration flow, polling, sensors, and data-driven models."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from custom_components.growatt_solar.const import (
    CONF_ADDRESS_OFFSET,
    CONF_HOST,
    CONF_MODEL,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DOMAIN,
)
from custom_components.growatt_solar.errors import DeviceReadError
from custom_components.growatt_solar.frontend import async_register_schedule_card
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.fakes import ClientFactory, ascii_registers, raise_transport

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry_data(model_id: str = "sph5000_tl_bl_up") -> dict[str, object]:
    return {
        CONF_MODEL: model_id,
        CONF_HOST: "192.0.2.15",
        CONF_PORT: 502,
        CONF_UNIT_ID: 1,
        CONF_ADDRESS_OFFSET: 0,
    }


def _patch_client(monkeypatch: pytest.MonkeyPatch, factory: ClientFactory) -> None:
    monkeypatch.setattr(
        "custom_components.growatt_solar.modbus_client.create_client",
        factory,
    )


def _sph_blocks() -> dict[tuple[str, int], list[int]]:
    pv = [0] * 11
    pv[3] = 3700
    storage = [0] * 64
    storage[14] = 57  # register 1014, state of charge
    return {
        ("input", 0): pv,
        ("input", 1000): storage,
        ("holding", 9): ascii_registers("ZH1.0", 3),
        ("holding", 23): ascii_registers("AB12345678", 5),
    }


def _selector_values(schema: object) -> list[str]:
    for key, value in schema.schema.items():  # type: ignore[attr-defined]
        if getattr(key, "schema", None) == CONF_MODEL:
            return [option["value"] for option in value.config["options"]]
    raise AssertionError("model selector was not in the form")


async def _state(hass: HomeAssistant, entry_id: str, entity_key: str) -> tuple[str, object]:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{entity_key}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return entity_id, state


async def test_user_flow_rejects_a_bad_host_and_a_timeout(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, ClientFactory(failure=raise_transport))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    assert "sph5000_tl_bl_up" in _selector_values(result["data_schema"])

    invalid = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**_entry_data(), CONF_HOST: "bad host"},
    )
    assert invalid["errors"]["host"] == "invalid_host"

    timed_out = await hass.config_entries.flow.async_configure(
        invalid["flow_id"],
        _entry_data(),
    )
    assert timed_out["errors"]["base"] == "timeout"


async def test_duplicate_endpoint_is_rejected(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, ClientFactory())
    existing = MockConfigEntry(domain=DOMAIN, data=_entry_data(), unique_id="192.0.2.15:502:1")
    existing.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    aborted = await hass.config_entries.flow.async_configure(result["flow_id"], _entry_data())
    assert aborted["type"] == "abort"
    assert aborted["reason"] == "already_configured"


async def test_setup_creates_sensors_and_unloads_the_connection(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(_sph_blocks())
    _patch_client(monkeypatch, factory)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={CONF_SCAN_INTERVAL: 30})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    del entity_id
    assert voltage.state == "370.0"
    assert voltage.attributes["unit_of_measurement"] == "V"
    assert voltage.attributes["device_class"] == "voltage"
    assert voltage.attributes["state_class"] == "measurement"

    _, charge = await _state(hass, entry.entry_id, "battery_state_of_charge")
    assert float(charge.state) == 57
    assert charge.attributes["device_class"] == "battery"
    assert charge.attributes["unit_of_measurement"] == "%"

    _, imported = await _state(hass, entry.entry_id, "grid_import_power")
    assert imported.attributes["device_class"] == "power"
    assert imported.attributes["unit_of_measurement"] == "W"
    _, produced = await _state(hass, entry.entry_id, "ac_output_energy_total")
    assert produced.attributes["device_class"] == "energy"
    assert produced.attributes["unit_of_measurement"] == "kWh"
    assert produced.attributes["state_class"] == "total_increasing"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.manufacturer == "Growatt"
    assert device.model == "SPH 5000TL BL-UP"
    assert device.serial_number == "AB12345678"
    assert device.sw_version == "ZH1.0"

    registry = er.async_get(hass)
    fault_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_system_fault_word_0"
    )
    assert fault_id is not None
    assert registry.async_get(fault_id).disabled_by is not None


async def test_diagnostics_redact_the_serial_number(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(_sph_blocks())
    _patch_client(monkeypatch, factory)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={CONF_SCAN_INTERVAL: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.growatt_solar.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["readings"]["serial_number"] == "**REDACTED**"
    assert "AB12345678" not in json.dumps(payload)
    assert payload["group_errors"] == {}
    assert factory.clients[0].closed is False

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert factory.clients[0].closed is True


async def test_connection_loss_retries_and_then_recovers(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(failure=raise_transport)
    _patch_client(monkeypatch, factory)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={CONF_SCAN_INTERVAL: 30})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert factory.clients[0].closed is True

    factory.failure = None
    factory.blocks.update(_sph_blocks())
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "370.0"


async def test_partial_group_failure_keeps_the_other_sensors(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_storage(function: str, address: int, count: int) -> Exception | None:
        del function, count
        if address == 1000:
            return DeviceReadError("the inverter returned a Modbus exception")
        return None

    factory = ClientFactory(_sph_blocks(), failure=fail_storage)
    _patch_client(monkeypatch, factory)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={CONF_SCAN_INTERVAL: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "370.0"
    _, battery = await _state(hass, entry.entry_id, "battery_voltage")
    assert battery.state == "unavailable"

    factory.clients[-1].failure = None
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    _, battery = await _state(hass, entry.entry_id, "battery_voltage")
    assert battery.state == "0.0"
    assert factory.clients[-1].max_in_flight == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


async def _setup_with_clock(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, factory: ClientFactory
) -> tuple[MockConfigEntry, _Clock]:
    clock = _Clock()
    monkeypatch.setattr("custom_components.growatt_solar.coordinator.time", clock)
    _patch_client(monkeypatch, factory)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={CONF_SCAN_INTERVAL: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, clock


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_later_polls_keep_live_measurements_and_rotate_settings(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(_sph_blocks())
    entry, _clock = await _setup_with_clock(hass, monkeypatch, factory)
    client = factory.clients[-1]
    client.calls.clear()

    await _refresh(hass, entry)

    addresses = [address for _function, address, _count in client.calls]
    assert 0 in addresses
    assert 1000 in addresses
    assert 1044 not in addresses
    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "370.0"


async def test_a_failed_poll_keeps_the_previous_value(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(_sph_blocks())
    entry, clock = await _setup_with_clock(hass, monkeypatch, factory)

    factory.clients[-1].failure = raise_transport
    clock.now += 40
    await _refresh(hass, entry)
    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "370.0"
    coordinator = entry.runtime_data
    assert coordinator.last_update_success is True
    assert coordinator.held_ages["pv1_voltage"] == 40

    from custom_components.growatt_solar.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["held_readings"] > 0
    assert payload["oldest_held_seconds"] == 40


async def test_held_values_expire_after_two_minutes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(_sph_blocks())
    entry, clock = await _setup_with_clock(hass, monkeypatch, factory)

    factory.clients[-1].failure = raise_transport
    clock.now += 119
    await _refresh(hass, entry)
    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "370.0"

    clock.now += 2
    await _refresh(hass, entry)
    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "unavailable"
    assert entry.runtime_data.last_update_success is False


async def test_a_failed_battery_group_holds_battery_and_keeps_pv_live(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_storage(function: str, address: int, count: int) -> Exception | None:
        del function, count
        if address == 1000:
            return DeviceReadError("the inverter returned a Modbus exception")
        return None

    factory = ClientFactory(_sph_blocks())
    entry, clock = await _setup_with_clock(hass, monkeypatch, factory)

    factory.clients[-1].failure = fail_storage
    factory.blocks[("input", 0)][3] = 3800
    clock.now += 30
    await _refresh(hass, entry)
    _, voltage = await _state(hass, entry.entry_id, "pv1_voltage")
    assert voltage.state == "380.0"
    _, charge = await _state(hass, entry.entry_id, "battery_state_of_charge")
    assert float(charge.state) == 57
    held = entry.runtime_data.held_ages
    assert "battery_state_of_charge" in held
    assert "pv1_voltage" not in held


async def test_a_decode_failure_is_not_replaced_with_an_old_value(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = _sph_blocks()
    blocks[("input", 0)][0] = 1
    factory = ClientFactory(blocks)
    entry, clock = await _setup_with_clock(hass, monkeypatch, factory)
    _, run_state = await _state(hass, entry.entry_id, "inverter_run_state")
    assert run_state.state == "normal"

    factory.blocks[("input", 0)][0] = 2
    clock.now += 30
    await _refresh(hass, entry)
    _, run_state = await _state(hass, entry.entry_id, "inverter_run_state")
    assert run_state.state == "unavailable"

    factory.clients[-1].failure = raise_transport
    clock.now += 30
    await _refresh(hass, entry)
    _, run_state = await _state(hass, entry.entry_id, "inverter_run_state")
    assert run_state.state == "unavailable"


async def test_overlapping_refreshes_do_not_run_two_polls(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ClientFactory(_sph_blocks(), pause=0.02)
    _patch_client(monkeypatch, factory)
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(), options={CONF_SCAN_INTERVAL: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    import asyncio

    await asyncio.gather(
        coordinator._async_update_data(),
        coordinator._async_update_data(),
    )
    assert factory.clients[-1].max_in_flight == 1


def _synthetic_payload() -> dict:
    return {
        "model_id": "synthetic_test_inverter",
        "model_name": "Synthetic Test Inverter",
        "manufacturer": "Example Works",
        "family": "Synthetic",
        "variants": ["Synthetic 1"],
        "documentation": [
            {
                "title": "Synthetic register note",
                "revision": "1",
                "url": "https://example.com/synthetic.pdf",
                "retrieved": "2026-10-11",
            }
        ],
        "connection": {
            "transport": "modbus_tcp",
            "default_port": 1502,
            "default_unit_id": 4,
            "default_scan_interval": 30,
            "minimum_command_interval": 0,
            "response_timeout": 5,
            "notes": "Synthetic model used to prove mappings are data.",
        },
        "read_groups": [
            {
                "id": "synthetic_input",
                "function": "input",
                "address": 20,
                "count": 2,
                "optional": False,
            }
        ],
        "registers": [
            {
                "entity_key": "synthetic_bus_voltage",
                "name": "Synthetic bus voltage",
                "address": 20,
                "function": "input",
                "width": 1,
                "data_type": "uint16",
                "word_order": "big",
                "scale": 0.02,
                "offset": 0,
                "device_class": "voltage",
                "state_class": "measurement",
                "unit": "V",
                "precision": 2,
                "read_group": "synthetic_input",
                "documented_name": "Vsynthetic",
                "meaning": "A voltage that exists only in this test mapping.",
                "source": "Synthetic register note, p. 1",
                "verification": "documented",
            },
            {
                "entity_key": "synthetic_temperature",
                "name": "Synthetic temperature",
                "address": 21,
                "function": "input",
                "width": 1,
                "data_type": "int16",
                "word_order": "big",
                "scale": 0.1,
                "offset": 0,
                "device_class": "temperature",
                "state_class": "measurement",
                "unit": "°C",
                "precision": 1,
                "read_group": "synthetic_input",
                "documented_name": "Tsynthetic",
                "meaning": "A signed temperature that exists only in this test mapping.",
                "source": "Synthetic register note, p. 1",
                "verification": "documented",
            },
        ],
    }


async def test_a_new_mapping_file_is_usable_without_code_changes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "synthetic_test_inverter.json").write_text(
        json.dumps(_synthetic_payload()),
        encoding="utf-8",
    )
    (tmp_path / "ignored.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        "custom_components.growatt_solar.register_map.registers_directory",
        lambda: tmp_path,
    )
    blocks = {("input", 20): [250, 0xFF9C]}
    _patch_client(monkeypatch, ClientFactory(blocks))

    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    options = _selector_values(form["data_schema"])
    assert options == ["synthetic_test_inverter"]
    assert "ignored.json" in form["description_placeholders"]["details"]

    created = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_MODEL: "synthetic_test_inverter",
            CONF_HOST: "192.0.2.40",
            CONF_PORT: 1502,
            CONF_UNIT_ID: 4,
            CONF_ADDRESS_OFFSET: 0,
        },
    )
    assert created["type"] == "create_entry"
    await hass.async_block_till_done()

    entry = created["result"]
    _, voltage = await _state(hass, entry.entry_id, "synthetic_bus_voltage")
    assert voltage.state == "5.0"
    assert voltage.attributes["unit_of_measurement"] == "V"
    _, temperature = await _state(hass, entry.entry_id, "synthetic_temperature")
    assert temperature.state == "-10.0"
    assert temperature.attributes["device_class"] == "temperature"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.model == "Synthetic Test Inverter"
    assert device.manufacturer == "Example Works"


async def test_reconfigure_updates_the_host(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, ClientFactory(_sph_blocks()))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_entry_data(),
        options={CONF_SCAN_INTERVAL: 30},
        unique_id="AB12345678",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    updated = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**_entry_data(), CONF_HOST: "192.0.2.16"},
    )
    assert updated["type"] == "abort"
    assert updated["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.0.2.16"


async def test_the_schedule_card_loads_with_the_integration(hass: HomeAssistant) -> None:
    published: list[StaticPathConfig] = []

    async def register(configs: list[StaticPathConfig]) -> None:
        published.extend(configs)

    hass.http = SimpleNamespace(async_register_static_paths=register)  # type: ignore[assignment]
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_args: None, [])

    await async_register_schedule_card(hass)

    assert published[0].url_path == "/growatt_solar_static"
    card_urls = [
        url
        for url in hass.data[DATA_EXTRA_MODULE_URL].urls
        if url.startswith("/growatt_solar_static/schedule-card.js?v=")
    ]
    assert len(card_urls) == 1
    assert len(card_urls[0].rsplit("=", 1)[-1]) == 12
