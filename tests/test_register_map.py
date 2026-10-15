"""Register-map discovery and validation."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.growatt_solar.register_map import load_catalog, load_model_file

ROOT = Path(__file__).resolve().parents[1]


def _mapping(**overrides: object) -> dict:
    payload: dict = {
        "model_id": "example_inverter",
        "model_name": "Example Inverter",
        "manufacturer": "Growatt",
        "family": "Example",
        "variants": ["Example 1"],
        "documentation": [
            {
                "title": "Example protocol",
                "revision": "1",
                "url": "https://example.com/protocol.pdf",
                "retrieved": "2026-10-11",
            }
        ],
        "connection": {
            "transport": "modbus_tcp",
            "default_port": 502,
            "default_unit_id": 1,
            "default_scan_interval": 30,
            "minimum_command_interval": 0,
            "response_timeout": 5,
            "notes": "Test mapping.",
        },
        "read_groups": [
            {
                "id": "measurements",
                "function": "input",
                "address": 10,
                "count": 2,
                "optional": False,
            }
        ],
        "registers": [_voltage()],
    }
    payload.update(overrides)
    return payload


def _voltage(**overrides: object) -> dict:
    register = {
        "entity_key": "bus_voltage",
        "name": "Bus voltage",
        "address": 10,
        "function": "input",
        "width": 1,
        "data_type": "uint16",
        "word_order": "big",
        "scale": 0.1,
        "offset": 0,
        "device_class": "voltage",
        "state_class": "measurement",
        "unit": "V",
        "precision": 1,
        "read_group": "measurements",
        "documented_name": "Vbus",
        "meaning": "Bus voltage.",
        "source": "Example protocol, p. 1",
        "verification": "documented",
    }
    register.update(overrides)
    return register


def _write(directory: Path, name: str, payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (directory / name).write_text(text, encoding="utf-8")


def test_shipped_sph_map_loads_without_claiming_a_bench_test() -> None:
    catalog = load_catalog()
    assert catalog.issues == ()
    model = catalog.models["sph5000_tl_bl_up"]
    assert model.model_name == "SPH 5000TL BL-UP"
    assert {item.entity_key for item in model.registers} >= {
        "pv1_voltage",
        "battery_state_of_charge",
        "grid_import_power",
        "battery_charge_power",
        "battery_discharge_power",
    }
    assert "experimentally_verified" not in model.verification_counts()
    table = (ROOT / "docs" / "sph5000-register-table.md").read_text(encoding="utf-8")
    strings = json.loads(
        (ROOT / "custom_components/growatt_solar/strings.json").read_text(encoding="utf-8")
    )
    for register in model.registers:
        assert register.name in table
        assert register.documented_name in table
        assert strings["entity"]["sensor"][register.entity_key]["name"] == register.name
    assert len(model.registers) == 70
    assert model.minimum_scan_interval() == 1
    assert model.connection.default_scan_interval == 1
    assert {control.entity_key for control in model.controls} >= {
        "energy_priority",
        "charge_period_1_start",
        "grid_period_1",
        "stop_charging_at",
    }
    priority = next(
        control for control in model.controls if control.entity_key == "energy_priority"
    )
    assert [option.label for option in priority.options] == [
        "Load first",
        "Battery first",
        "Grid first",
    ]
    for control in model.controls:
        assert strings["entity"][control.platform][control.entity_key]["name"] == control.name


def test_invalid_files_are_excluded_and_valid_models_remain(tmp_path: Path) -> None:
    _write(tmp_path, "example_inverter.json", _mapping())
    _write(tmp_path, "broken.json", "{")
    _write(tmp_path, "missing.json", {"model_id": "missing"})
    catalog = load_catalog(tmp_path)
    assert list(catalog.models) == ["example_inverter"]
    filenames = {issue.filename for issue in catalog.issues}
    assert filenames == {"broken.json", "missing.json"}
    assert any("invalid JSON" in issue.message for issue in catalog.issues)
    assert any(issue.filename == "missing.json" for issue in catalog.issues)


def test_schema_rejects_unsafe_register_definitions(tmp_path: Path) -> None:
    cases = {
        "duplicate_entity.json": _mapping(
            model_id="duplicate_entity",
            registers=[_voltage(), _voltage()],
        ),
        "bad_width.json": _mapping(
            model_id="bad_width",
            registers=[_voltage(width=4, data_type="uint16")],
        ),
        "float_type.json": _mapping(
            model_id="float_type",
            registers=[_voltage(data_type="float32", width=2)],
        ),
        "middle_endian.json": _mapping(
            model_id="middle_endian",
            registers=[_voltage(word_order="middle")],
        ),
        "zero_scale.json": _mapping(
            model_id="zero_scale",
            registers=[_voltage(scale=0)],
        ),
        "energy_as_power.json": _mapping(
            model_id="energy_as_power",
            registers=[
                _voltage(
                    entity_key="output_power",
                    name="Output power",
                    device_class="power",
                    state_class="total_increasing",
                    unit="kWh",
                )
            ],
        ),
        "outside_group.json": _mapping(
            model_id="outside_group",
            registers=[_voltage(address=80)],
        ),
        "huge_group.json": _mapping(
            model_id="huge_group",
            read_groups=[
                {
                    "id": "measurements",
                    "function": "input",
                    "address": 0,
                    "count": 126,
                    "optional": False,
                }
            ],
        ),
        "overlap.json": _mapping(
            model_id="overlap",
            read_groups=[
                {
                    "id": "measurements",
                    "function": "input",
                    "address": 10,
                    "count": 2,
                    "optional": False,
                }
            ],
            registers=[
                _voltage(),
                _voltage(entity_key="other_voltage", name="Other voltage", address=10),
            ],
        ),
    }
    for filename, payload in cases.items():
        _write(tmp_path, filename, payload)
    catalog = load_catalog(tmp_path)
    assert catalog.models == {}
    messages = " ".join(issue.message for issue in catalog.issues)
    assert "duplicate entity id" in messages
    assert "does not match uint16" in messages
    assert "not supported" in messages
    assert "word_order" in messages
    assert "scale must not be 0" in messages
    assert "device class does not match unit" in messages
    assert "falls outside its read group" in messages
    assert "count must be between 1 and 125" in messages
    assert "overlapping register" in messages


def test_filename_must_match_the_model_id(tmp_path: Path) -> None:
    _write(tmp_path, "wrong_name.json", _mapping())
    catalog = load_catalog(tmp_path)
    assert catalog.models == {}
    assert catalog.issues[0].filename == "wrong_name.json"
    assert "example_inverter.json" in catalog.issues[0].message


def test_load_model_file_returns_the_selector_metadata(tmp_path: Path) -> None:
    path = tmp_path / "example_inverter.json"
    path.write_text(json.dumps(_mapping()), encoding="utf-8")
    model = load_model_file(path)
    assert model.connection.default_port == 502
    assert model.registers[0].entity_key == "bus_voltage"
