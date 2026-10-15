"""Schedule and priority settings."""

from __future__ import annotations

from datetime import time

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
from custom_components.growatt_solar.setting import pack_clock, unpack_clock
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.fakes import ClientFactory

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def test_a_clock_is_packed_as_hour_and_minute_bytes() -> None:
    assert pack_clock(time(1, 30, 45)) == 0x011E
    assert unpack_clock(0x011E) == time(1, 30)
    assert unpack_clock((23 << 8) | 59) == time(23, 59)
    assert unpack_clock(0x1800) is None
    assert unpack_clock(0x003C) is None


async def test_settings_round_trip_through_the_clock_and_priority_controls(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = {
        ("holding", 1044): [2],
        ("holding", 1090): [80, 95, 1],
        ("holding", 1100): [(1 << 8) | 30, (6 << 8) | 48, 1, 0, 0, 0, 0, 0, 0],
    }
    factory = ClientFactory(blocks)
    monkeypatch.setattr("custom_components.growatt_solar.modbus_client.create_client", factory)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODEL: "sph5000_tl_bl_up",
            CONF_HOST: "192.0.2.40",
            CONF_PORT: 502,
            CONF_UNIT_ID: 1,
            CONF_ADDRESS_OFFSET: 0,
        },
        options={CONF_SCAN_INTERVAL: 30},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    client = factory.clients[-1]
    assert client.writes == []
    priority_id, priority = _entity(hass, "select", entry.entry_id, "energy_priority")
    start_id, start = _entity(hass, "time", entry.entry_id, "charge_period_1_start")
    period_id, period = _entity(hass, "switch", entry.entry_id, "charge_period_1")
    stop_id, stop = _entity(hass, "number", entry.entry_id, "stop_charging_at")
    assert priority.state == "Grid first"
    assert start.state == "01:30:00"
    assert period.state == "on"
    assert float(stop.state) == 95
    registry = er.async_get(hass)
    priority_entry = registry.async_get(priority_id)
    start_entry = registry.async_get(start_id)
    assert priority_entry is not None and start_entry is not None
    assert priority_entry.entity_category == EntityCategory.CONFIG
    assert start_entry.entity_category == EntityCategory.CONFIG

    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": start_id, "time": "23:15:00"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": priority_id, "option": "Battery first"},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": period_id},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": stop_id, "value": 80},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert client.block_writes == [
        (1100, [(23 << 8) | 15, (6 << 8) | 48, 1]),
        (1100, [(23 << 8) | 15, (6 << 8) | 48, 0]),
    ]
    assert client.writes == [(1044, 1), (1091, 80)]
    assert _state(hass, start_id) == "23:15:00"
    assert _state(hass, priority_id) == "Battery first"
    assert _state(hass, period_id) == "off"
    assert float(_state(hass, stop_id)) == 80

    client.confirm_writes = False
    with pytest.raises(HomeAssistantError, match="instead of"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": priority_id, "option": "Load first"},
            blocking=True,
        )
    assert _state(hass, priority_id) == "Battery first"


async def test_a_window_is_written_as_one_schedule(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = {
        ("holding", 1100): [(1 << 8) | 30, (6 << 8) | 48, 1, 0, 0, 0, 0, 0, 0],
    }
    factory = ClientFactory(blocks)
    monkeypatch.setattr("custom_components.growatt_solar.modbus_client.create_client", factory)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODEL: "sph5000_tl_bl_up",
            CONF_HOST: "192.0.2.41",
            CONF_PORT: 502,
            CONF_UNIT_ID: 1,
            CONF_ADDRESS_OFFSET: 0,
        },
        options={CONF_SCAN_INTERVAL: 30},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    start_id, _start = _entity(hass, "time", entry.entry_id, "charge_period_1_start")
    end_id, _end = _entity(hass, "time", entry.entry_id, "charge_period_1_end")
    enable_id, _enable = _entity(hass, "switch", entry.entry_id, "charge_period_1")

    await hass.services.async_call(
        DOMAIN,
        "set_window",
        {
            "start_entity": start_id,
            "end_entity": end_id,
            "enable_entity": enable_id,
            "start": "04:06:00",
            "end": "08:00:00",
            "enabled": True,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    client = factory.clients[-1]
    assert client.block_writes == [(1100, [(4 << 8) | 6, 8 << 8, 1])]
    assert client.writes == []
    assert _state(hass, start_id) == "04:06:00"
    assert _state(hass, end_id) == "08:00:00"
    assert _state(hass, enable_id) == "on"


async def test_a_window_cannot_share_a_minute_with_another(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = {
        ("holding", 1080): [(4 << 8) | 2, 8 << 8, 1, 0, 0, 0, 0, 0, 0],
        ("holding", 1100): [(2 << 8), 4 << 8, 1, 0, 0, 0, 0, 0, 0],
    }
    factory = ClientFactory(blocks)
    monkeypatch.setattr("custom_components.growatt_solar.modbus_client.create_client", factory)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODEL: "sph5000_tl_bl_up",
            CONF_HOST: "192.0.2.42",
            CONF_PORT: 502,
            CONF_UNIT_ID: 1,
            CONF_ADDRESS_OFFSET: 0,
        },
        options={CONF_SCAN_INTERVAL: 30},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    start_id, _start = _entity(hass, "time", entry.entry_id, "grid_period_1_start")
    end_id, _end = _entity(hass, "time", entry.entry_id, "grid_period_1_end")
    enable_id, _enable = _entity(hass, "switch", entry.entry_id, "grid_period_1")

    with pytest.raises(HomeAssistantError, match="04:00 is already used by Charge period 1"):
        await hass.services.async_call(
            DOMAIN,
            "set_window",
            {
                "start_entity": start_id,
                "end_entity": end_id,
                "enable_entity": enable_id,
                "start": "04:00:00",
                "end": "08:00:00",
                "enabled": True,
            },
            blocking=True,
        )

    assert factory.clients[-1].block_writes == []
    assert _state(hass, start_id) == "04:02:00"


def _entity(hass: HomeAssistant, domain: str, entry_id: str, entity_key: str) -> tuple[str, State]:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry_id}_{entity_key}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return entity_id, state


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state
