"""Load and validate inverter register maps.

Model behavior lives in the JSON files under ``registers/``. Python rejects
maps it cannot apply safely and never keeps a hardcoded model list.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import MappingError

_LOGGER = logging.getLogger(__name__)

_MODEL_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTITY_KEY = _MODEL_ID
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TOP_LEVEL = {
    "model_id",
    "model_name",
    "manufacturer",
    "family",
    "variants",
    "documentation",
    "connection",
    "read_groups",
    "registers",
    "controls",
}
_CONTROL_PLATFORMS = {"select", "time", "switch", "number"}
_CONTROL_FIELDS = {
    "entity_key",
    "name",
    "platform",
    "address",
    "read_group",
    "icon",
    "source",
    "meaning",
    "options",
    "minimum",
    "maximum",
    "unit",
    "write_span",
}
_ICON = re.compile(r"^mdi:[a-z0-9-]+$")
_CONNECTION = {
    "transport",
    "default_port",
    "default_unit_id",
    "default_scan_interval",
    "minimum_command_interval",
    "response_timeout",
    "notes",
}
_DOCUMENT = {"title", "revision", "url", "retrieved", "role"}
_GROUP = {"id", "function", "address", "count", "optional"}
_REGISTER = {
    "entity_key",
    "name",
    "address",
    "function",
    "width",
    "data_type",
    "word_order",
    "scale",
    "offset",
    "device_class",
    "state_class",
    "unit",
    "precision",
    "entity_category",
    "enabled_default",
    "read_group",
    "documented_name",
    "meaning",
    "source",
    "verification",
    "value_map",
    "raw_min",
    "raw_max",
    "notes",
    "identity",
}

_FUNCTIONS = {"input", "holding"}
_VERIFICATIONS = {
    "documented",
    "documented_crosscheck",
    "documented_label_conflict",
    "documented_provisional",
}
_WORD_ORDERS = {"big", "little"}
_WIDTH_BY_TYPE = {
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
}
_STATE_CLASSES = {None, "measurement", "total", "total_increasing"}
_CATEGORIES = {None, "diagnostic", "config"}
_IDENTITIES = {None, "serial", "firmware"}
_DEVICE_CLASSES = {
    None,
    "voltage",
    "current",
    "power",
    "apparent_power",
    "frequency",
    "energy",
    "battery",
    "temperature",
    "duration",
    "enum",
}
_CLASS_UNITS = {
    "voltage": "V",
    "current": "A",
    "power": "W",
    "apparent_power": "VA",
    "frequency": "Hz",
    "energy": "kWh",
    "battery": "%",
    "temperature": "°C",
    "duration": "s",
    "enum": None,
}
_UNIT_CLASSES: dict[str, set[str | None]] = {
    "V": {"voltage"},
    "A": {"current"},
    "W": {"power"},
    "VA": {"apparent_power"},
    "Hz": {"frequency"},
    "kWh": {"energy"},
    "°C": {"temperature"},
    "%": {"battery", None},
    "s": {"duration"},
}


@dataclass(frozen=True, slots=True)
class DocumentationRef:
    """Where a statement in a mapping was taken from."""

    title: str
    revision: str
    url: str
    retrieved: str
    role: str | None


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    """Transport defaults supplied by a mapping file."""

    transport: str
    default_port: int
    default_unit_id: int
    default_scan_interval: int
    minimum_command_interval: float
    response_timeout: float
    notes: str


@dataclass(frozen=True, slots=True)
class ReadGroup:
    """One contiguous Modbus read."""

    group_id: str
    function: str
    address: int
    count: int
    optional: bool

    @property
    def end(self) -> int:
        """First address after this group."""
        return self.address + self.count


@dataclass(frozen=True, slots=True)
class RegisterDefinition:
    """One sensor, decoded from one mapped register span."""

    entity_key: str
    name: str
    address: int
    function: str
    width: int
    data_type: str
    word_order: str
    scale: float
    offset: float
    device_class: str | None
    state_class: str | None
    unit: str | None
    precision: int | None
    entity_category: str | None
    enabled_default: bool
    read_group: str
    documented_name: str
    meaning: str
    source: str
    verification: str
    value_map: dict[int, str] | None
    raw_min: int | None
    raw_max: int | None
    notes: str | None
    identity: str | None


@dataclass(frozen=True, slots=True)
class ControlOption:
    """One choice in a select setting."""

    value: int
    label: str


@dataclass(frozen=True, slots=True)
class ControlDefinition:
    """One configuration entity backed by a single holding register."""

    entity_key: str
    name: str
    platform: str
    address: int
    read_group: str
    icon: str
    source: str
    meaning: str
    options: tuple[ControlOption, ...]
    minimum: int | None
    maximum: int | None
    unit: str | None
    write_span: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class InverterModel:
    """A validated register map for one inverter model."""

    model_id: str
    model_name: str
    manufacturer: str
    family: str
    variants: tuple[str, ...]
    documentation: tuple[DocumentationRef, ...]
    connection: ConnectionProfile
    read_groups: dict[str, ReadGroup]
    registers: tuple[RegisterDefinition, ...]
    controls: tuple[ControlDefinition, ...]
    source_name: str

    def verification_counts(self) -> dict[str, int]:
        """Count registers by verification status."""
        counts: dict[str, int] = {}
        for register in self.registers:
            counts[register.verification] = counts.get(register.verification, 0) + 1
        return counts

    def minimum_scan_interval(self) -> int:
        """Shortest pause after a finished poll.

        The next poll starts only after this one finishes, and every command
        already waits minimum_command_interval. One more command interval is
        the shortest pause that stays within the protocol.
        """
        return max(1, math.ceil(self.connection.minimum_command_interval))


@dataclass(frozen=True, slots=True)
class MappingIssue:
    """A mapping file that was ignored, with the reason."""

    filename: str
    message: str


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Valid models and the files that were rejected."""

    models: dict[str, InverterModel]
    issues: tuple[MappingIssue, ...]

    def sorted_models(self) -> list[InverterModel]:
        """Models in display order."""
        return sorted(self.models.values(), key=lambda model: model.model_name.lower())


def registers_directory() -> Path:
    """Directory that ships with the integration."""
    return Path(__file__).resolve().parent / "registers"


def load_catalog(directory: Path | None = None) -> ModelCatalog:
    """Discover every JSON mapping in ``directory``."""
    folder = registers_directory() if directory is None else directory
    models: dict[str, InverterModel] = {}
    issues: list[MappingIssue] = []
    if not folder.is_dir():
        issues.append(MappingIssue(folder.name, "register directory does not exist"))
        return ModelCatalog(models, tuple(issues))

    for path in sorted(folder.glob("*.json")):
        try:
            model = load_model_file(path)
        except MappingError as err:
            for message in err.messages:
                issues.append(MappingIssue(path.name, message))
                _LOGGER.error("Ignoring %s: %s", path.name, message)
            continue
        if model.model_id in models:
            message = f"duplicate model id {model.model_id}"
            issues.append(MappingIssue(path.name, message))
            _LOGGER.error("Ignoring %s: %s", path.name, message)
            continue
        models[model.model_id] = model
    return ModelCatalog(models, tuple(issues))


def load_model_file(path: Path) -> InverterModel:
    """Parse and validate one mapping file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise MappingError([f"invalid JSON: {err.msg}"]) from err
    if not isinstance(payload, dict):
        raise MappingError(["mapping root must be an object"])
    return parse_model(payload, path.name)


def parse_model(payload: dict[str, Any], filename: str) -> InverterModel:
    """Validate a decoded mapping document."""
    errors: list[str] = []
    _reject_unknown(payload, _TOP_LEVEL, "mapping", errors)
    model_id = _expect_id(payload.get("model_id"), "model_id", errors)
    if model_id is not None and Path(filename).stem != model_id:
        errors.append(f"filename must be {model_id}.json")

    model_name = _expect_text(payload.get("model_name"), "model_name", errors)
    manufacturer = _expect_text(payload.get("manufacturer"), "manufacturer", errors)
    family = _expect_text(payload.get("family"), "family", errors)
    variants = _expect_text_list(payload.get("variants"), "variants", errors)
    documentation = _parse_documentation(payload.get("documentation"), errors)
    connection = _parse_connection(payload.get("connection"), errors)
    groups = _parse_groups(payload.get("read_groups"), errors)
    registers = _parse_registers(payload.get("registers"), groups, errors)
    controls = _parse_controls(payload.get("controls", []), groups, registers, errors)

    if errors:
        raise MappingError(errors)
    assert model_id and model_name and manufacturer and family
    assert variants is not None and documentation is not None
    assert connection is not None and groups is not None and registers is not None
    assert controls is not None
    return InverterModel(
        model_id=model_id,
        model_name=model_name,
        manufacturer=manufacturer,
        family=family,
        variants=tuple(variants),
        documentation=tuple(documentation),
        connection=connection,
        read_groups=groups,
        registers=tuple(registers),
        controls=tuple(controls),
        source_name=filename,
    )


def _parse_connection(value: Any, errors: list[str]) -> ConnectionProfile | None:
    if not isinstance(value, dict):
        errors.append("connection must be an object")
        return None
    _reject_unknown(value, _CONNECTION, "connection", errors)
    transport = value.get("transport")
    if transport != "modbus_tcp":
        errors.append("connection.transport must be modbus_tcp")
    port = _expect_int(value.get("default_port"), "connection.default_port", errors)
    unit_id = _expect_int(value.get("default_unit_id"), "connection.default_unit_id", errors)
    scan = _expect_int(
        value.get("default_scan_interval"),
        "connection.default_scan_interval",
        errors,
    )
    interval = _expect_number(
        value.get("minimum_command_interval"),
        "connection.minimum_command_interval",
        errors,
    )
    timeout = _expect_number(
        value.get("response_timeout"),
        "connection.response_timeout",
        errors,
    )
    notes = _expect_text(value.get("notes"), "connection.notes", errors)
    if port is not None and not 1 <= port <= 65535:
        errors.append("connection.default_port must be between 1 and 65535")
    if unit_id is not None and not 1 <= unit_id <= 254:
        errors.append("connection.default_unit_id must be between 1 and 254")
    if scan is not None and not 1 <= scan <= 300:
        errors.append("connection.default_scan_interval must be between 1 and 300")
    if interval is not None and not 0 <= interval <= 5:
        errors.append("connection.minimum_command_interval must be between 0 and 5")
    if timeout is not None and not 1 <= timeout <= 30:
        errors.append("connection.response_timeout must be between 1 and 30")
    if (
        transport != "modbus_tcp"
        or port is None
        or unit_id is None
        or scan is None
        or interval is None
        or timeout is None
        or notes is None
    ):
        return None
    return ConnectionProfile(transport, port, unit_id, scan, interval, timeout, notes)


def _parse_documentation(value: Any, errors: list[str]) -> list[DocumentationRef] | None:
    if not isinstance(value, list) or not value:
        errors.append("documentation must be a non-empty list")
        return None
    refs: list[DocumentationRef] = []
    for index, item in enumerate(value):
        label = f"documentation[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        _reject_unknown(item, _DOCUMENT, label, errors)
        title = _expect_text(item.get("title"), f"{label}.title", errors)
        revision = _expect_text(item.get("revision"), f"{label}.revision", errors)
        url = _expect_text(item.get("url"), f"{label}.url", errors)
        retrieved = _expect_text(item.get("retrieved"), f"{label}.retrieved", errors)
        role = item.get("role")
        if role is not None:
            role = _expect_text(role, f"{label}.role", errors)
        if url is not None and urlparse(url).scheme != "https":
            errors.append(f"{label}.url must use https")
        if retrieved is not None and (not _DATE.match(retrieved) or not _valid_date(retrieved)):
            errors.append(f"{label}.retrieved must be a YYYY-MM-DD date")
        if title and revision and url and retrieved:
            refs.append(DocumentationRef(title, revision, url, retrieved, role))
    return refs or None


def _parse_groups(value: Any, errors: list[str]) -> dict[str, ReadGroup] | None:
    if not isinstance(value, list) or not value:
        errors.append("read_groups must be a non-empty list")
        return None
    groups: dict[str, ReadGroup] = {}
    spans: list[tuple[str, str, int, int]] = []
    for index, item in enumerate(value):
        label = f"read_groups[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        _reject_unknown(item, _GROUP, label, errors)
        group_id = _expect_id(item.get("id"), f"{label}.id", errors)
        function = item.get("function")
        if function not in _FUNCTIONS:
            errors.append(f"{label}.function must be input or holding")
        address = _expect_int(item.get("address"), f"{label}.address", errors)
        count = _expect_int(item.get("count"), f"{label}.count", errors)
        optional = item.get("optional")
        if not isinstance(optional, bool):
            errors.append(f"{label}.optional must be a boolean")
            optional = None
        if address is not None and not 0 <= address <= 65535:
            errors.append(f"{label}.address is outside 0..65535")
        if count is not None and not 1 <= count <= 125:
            errors.append(f"{label}.count must be between 1 and 125")
        if (
            group_id is None
            or function not in _FUNCTIONS
            or address is None
            or count is None
            or not isinstance(optional, bool)
            or not 0 <= address <= 65535
            or not 1 <= count <= 125
        ):
            continue
        if address + count > 65536:
            errors.append(f"{label} extends past the Modbus address space")
            continue
        if group_id in groups:
            errors.append(f"duplicate read group {group_id}")
            continue
        groups[group_id] = ReadGroup(group_id, function, address, count, optional)
        spans.append((function, group_id, address, address + count))
    _reject_overlaps(spans, "read group", errors)
    return groups or None


def _parse_registers(
    value: Any,
    groups: dict[str, ReadGroup] | None,
    errors: list[str],
) -> list[RegisterDefinition] | None:
    if not isinstance(value, list) or not value:
        errors.append("registers must be a non-empty list")
        return None
    registers: list[RegisterDefinition] = []
    seen_keys: set[str] = set()
    spans: list[tuple[str, str, int, int]] = []
    identities: dict[str, str] = {}
    for index, item in enumerate(value):
        label = f"registers[{index}]"
        parsed = _parse_register(item, label, groups, errors)
        if parsed is None:
            continue
        if parsed.entity_key in seen_keys:
            errors.append(f"duplicate entity id {parsed.entity_key}")
            continue
        seen_keys.add(parsed.entity_key)
        if parsed.identity is not None:
            previous = identities.get(parsed.identity)
            if previous is not None:
                errors.append(
                    f"identity {parsed.identity} is used by both {previous} and {parsed.entity_key}"
                )
            identities[parsed.identity] = parsed.entity_key
        registers.append(parsed)
        spans.append(
            (parsed.function, parsed.entity_key, parsed.address, parsed.address + parsed.width)
        )
    _reject_overlaps(spans, "register", errors)
    return registers or None


def _parse_controls(
    value: Any,
    groups: dict[str, ReadGroup] | None,
    registers: list[RegisterDefinition] | None,
    errors: list[str],
) -> list[ControlDefinition] | None:
    if not isinstance(value, list):
        errors.append("controls must be a list")
        return None
    controls: list[ControlDefinition] = []
    seen_keys = {register.entity_key for register in registers} if registers else set()
    seen_addresses: set[int] = set()
    for index, item in enumerate(value):
        label = f"controls[{index}]"
        parsed = _parse_control(item, label, groups, errors)
        if parsed is None:
            continue
        if parsed.entity_key in seen_keys:
            errors.append(f"duplicate entity id {parsed.entity_key}")
            continue
        if parsed.address in seen_addresses:
            errors.append(f"duplicate control address {parsed.address}")
            continue
        seen_keys.add(parsed.entity_key)
        seen_addresses.add(parsed.address)
        controls.append(parsed)
    _check_write_spans(controls, errors)
    return controls


def _parse_control(
    item: Any,
    label: str,
    groups: dict[str, ReadGroup] | None,
    errors: list[str],
) -> ControlDefinition | None:
    if not isinstance(item, dict):
        errors.append(f"{label} must be an object")
        return None
    _reject_unknown(item, _CONTROL_FIELDS, label, errors)
    entity_key = _expect_id(item.get("entity_key"), f"{label}.entity_key", errors)
    name = _expect_text(item.get("name"), f"{label}.name", errors)
    platform = item.get("platform")
    if platform not in _CONTROL_PLATFORMS:
        errors.append(f"{label}.platform must be select, time, switch, or number")
        platform = None
    address = _expect_int(item.get("address"), f"{label}.address", errors)
    read_group = _expect_id(item.get("read_group"), f"{label}.read_group", errors)
    icon = _expect_text(item.get("icon"), f"{label}.icon", errors)
    if icon is not None and not _ICON.match(icon):
        errors.append(f"{label}.icon must be an mdi icon name")
        icon = None
    source = _expect_text(item.get("source"), f"{label}.source", errors)
    meaning = _expect_text(item.get("meaning"), f"{label}.meaning", errors)
    options = _parse_control_options(item, label, platform, errors)
    minimum, maximum, unit = _parse_control_range(item, label, platform, errors)
    write_span, span_ok = _parse_write_span(item, label, errors)
    group = groups.get(read_group) if groups is not None and read_group is not None else None
    if read_group is not None and groups is not None and group is None:
        errors.append(f"{label}.read_group {read_group} does not exist")
    if group is not None and group.function != "holding":
        errors.append(f"{label} must use a holding read group")
    if group is not None and address is not None and not group.address <= address < group.end:
        errors.append(f"{label} falls outside its read group")
    if platform == "number" and (minimum is None or maximum is None or unit is None):
        return None
    if not span_ok:
        return None
    if (
        write_span is not None
        and address is not None
        and group is not None
        and not group.address <= write_span[0] < write_span[0] + write_span[1] <= group.end
    ):
        errors.append(f"{label}.write_span falls outside its read group")
        return None
    if (
        write_span is not None
        and address is not None
        and not write_span[0] <= address < write_span[0] + write_span[1]
    ):
        errors.append(f"{label} falls outside its write span")
        return None
    if (
        entity_key is None
        or name is None
        or platform is None
        or address is None
        or read_group is None
        or icon is None
        or source is None
        or meaning is None
        or options is None
        or group is None
        or group.function != "holding"
        or not group.address <= address < group.end
    ):
        return None
    return ControlDefinition(
        entity_key,
        name,
        platform,
        address,
        read_group,
        icon,
        source,
        meaning,
        tuple(options),
        minimum,
        maximum,
        unit,
        write_span,
    )


def _parse_write_span(
    item: dict[str, Any],
    label: str,
    errors: list[str],
) -> tuple[tuple[int, int] | None, bool]:
    if "write_span" not in item:
        return None, True
    raw = item["write_span"]
    span_label = f"{label}.write_span"
    if not isinstance(raw, dict):
        errors.append(f"{span_label} must be an object")
        return None, False
    _reject_unknown(raw, {"address", "count"}, span_label, errors)
    address = _expect_int(raw.get("address"), f"{span_label}.address", errors)
    count = _expect_int(raw.get("count"), f"{span_label}.count", errors)
    if address is None or count is None:
        return None, False
    if not 0 <= address <= 65535 or not 1 <= count <= 125 or address + count > 65536:
        errors.append(f"{span_label} is outside the Modbus address space")
        return None, False
    return (address, count), True


def _check_write_spans(controls: list[ControlDefinition], errors: list[str]) -> None:
    grouped: dict[tuple[int, int], list[ControlDefinition]] = {}
    for control in controls:
        if control.write_span is None:
            continue
        grouped.setdefault(control.write_span, []).append(control)
    for (address, count), members in grouped.items():
        covered = {control.address for control in members}
        expected = set(range(address, address + count))
        if covered != expected:
            errors.append(f"write span {address}+{count} does not cover every register in it")


def _parse_control_options(
    item: dict[str, Any],
    label: str,
    platform: str | None,
    errors: list[str],
) -> list[ControlOption] | None:
    raw_options = item.get("options")
    if platform != "select":
        if raw_options is not None:
            errors.append(f"{label}.options is only valid for a select")
        return [] if platform is not None else None
    if not isinstance(raw_options, list) or not raw_options:
        errors.append(f"{label}.options must be a non-empty list")
        return None
    options: list[ControlOption] = []
    seen_values: set[int] = set()
    seen_labels: set[str] = set()
    for index, option in enumerate(raw_options):
        option_label = f"{label}.options[{index}]"
        if not isinstance(option, dict):
            errors.append(f"{option_label} must be an object")
            continue
        _reject_unknown(option, {"value", "label"}, option_label, errors)
        value = _expect_int(option.get("value"), f"{option_label}.value", errors)
        text = _expect_text(option.get("label"), f"{option_label}.label", errors)
        if value is None or text is None:
            continue
        if not 0 <= value <= 0xFFFF:
            errors.append(f"{option_label}.value is outside 0..65535")
            continue
        if value in seen_values or text in seen_labels:
            errors.append(f"{option_label} repeats a value or label")
            continue
        seen_values.add(value)
        seen_labels.add(text)
        options.append(ControlOption(value, text))
    return options or None


def _parse_control_range(
    item: dict[str, Any],
    label: str,
    platform: str | None,
    errors: list[str],
) -> tuple[int | None, int | None, str | None]:
    minimum = item.get("minimum")
    maximum = item.get("maximum")
    unit = item.get("unit")
    if platform != "number":
        if minimum is not None or maximum is not None or unit is not None:
            errors.append(f"{label} range fields are only valid for a number")
        return None, None, None
    parsed_min = _expect_int(minimum, f"{label}.minimum", errors)
    parsed_max = _expect_int(maximum, f"{label}.maximum", errors)
    parsed_unit = _expect_text(unit, f"{label}.unit", errors)
    if parsed_unit is not None and parsed_unit != "%":
        errors.append(f"{label}.unit must be %")
        parsed_unit = None
    if (
        parsed_min is not None
        and parsed_max is not None
        and not 0 <= parsed_min <= parsed_max <= 0xFFFF
    ):
        errors.append(f"{label} range must sit inside 0..65535")
        return None, None, parsed_unit
    return parsed_min, parsed_max, parsed_unit


def _parse_register(
    item: Any,
    label: str,
    groups: dict[str, ReadGroup] | None,
    errors: list[str],
) -> RegisterDefinition | None:
    if not isinstance(item, dict):
        errors.append(f"{label} must be an object")
        return None
    _reject_unknown(item, _REGISTER, label, errors)
    entity_key = _expect_id(item.get("entity_key"), f"{label}.entity_key", errors)
    name = _expect_text(item.get("name"), f"{label}.name", errors)
    address = _expect_int(item.get("address"), f"{label}.address", errors)
    function = item.get("function")
    if function not in _FUNCTIONS:
        errors.append(f"{label}.function must be input or holding")
    width = _expect_int(item.get("width"), f"{label}.width", errors)
    data_type = item.get("data_type")
    if data_type not in _WIDTH_BY_TYPE and data_type != "ascii":
        errors.append(f"{label}.data_type is not supported")
    word_order = item.get("word_order", "big")
    if word_order not in _WORD_ORDERS:
        errors.append(f"{label}.word_order must be big or little")
    scale = _expect_number(item.get("scale"), f"{label}.scale", errors)
    offset = _expect_number(item.get("offset", 0), f"{label}.offset", errors)
    device_class = item.get("device_class")
    if device_class not in _DEVICE_CLASSES:
        errors.append(f"{label}.device_class is not supported")
    state_class = item.get("state_class")
    if state_class not in _STATE_CLASSES:
        errors.append(f"{label}.state_class is not supported")
    unit = item.get("unit")
    if unit not in {None, *_CLASS_UNITS.values(), "%"}:
        errors.append(f"{label}.unit is not supported")
    precision = item.get("precision")
    if precision is not None:
        precision = _expect_int(precision, f"{label}.precision", errors)
        if precision is not None and not 0 <= precision <= 6:
            errors.append(f"{label}.precision must be between 0 and 6")
    category = item.get("entity_category")
    if category not in _CATEGORIES:
        errors.append(f"{label}.entity_category is not supported")
    enabled_default = item.get("enabled_default", True)
    if not isinstance(enabled_default, bool):
        errors.append(f"{label}.enabled_default must be a boolean")
        enabled_default = True
    read_group = item.get("read_group")
    if not isinstance(read_group, str) or not read_group:
        errors.append(f"{label}.read_group must be a string")
        read_group = None
    documented_name = _expect_text(item.get("documented_name"), f"{label}.documented_name", errors)
    meaning = _expect_text(item.get("meaning"), f"{label}.meaning", errors)
    source = _expect_text(item.get("source"), f"{label}.source", errors)
    verification = item.get("verification")
    if verification not in _VERIFICATIONS:
        errors.append(f"{label}.verification is not supported")
    raw_min = _optional_int(item.get("raw_min"), f"{label}.raw_min", errors)
    raw_max = _optional_int(item.get("raw_max"), f"{label}.raw_max", errors)
    notes = item.get("notes")
    if notes is not None:
        notes = _expect_text(notes, f"{label}.notes", errors)
    identity = item.get("identity")
    if identity not in _IDENTITIES:
        errors.append(f"{label}.identity is not supported")
    value_map = _parse_value_map(item.get("value_map"), label, errors)

    if scale is not None and scale == 0:
        errors.append(f"{label}.scale must not be 0")
    if data_type == "ascii":
        if width is not None and not 1 <= width <= 16:
            errors.append(f"{label}.width for ascii must be between 1 and 16")
        if scale not in (None, 1):
            errors.append(f"{label}.scale for ascii must be 1")
        if offset not in (None, 0):
            errors.append(f"{label}.offset for ascii must be 0")
        if device_class is not None or state_class is not None or unit is not None:
            errors.append(f"{label} ascii values cannot have a device class, state class, or unit")
        if (
            precision is not None
            or value_map is not None
            or raw_min is not None
            or raw_max is not None
        ):
            errors.append(f"{label} ascii values cannot set precision, value_map, or raw limits")
    elif data_type in _WIDTH_BY_TYPE and width is not None and width != _WIDTH_BY_TYPE[data_type]:
        errors.append(f"{label}.width does not match {data_type}")
    if width is not None and width > 1 and "word_order" not in item:
        errors.append(f"{label}.word_order is required for multi-register values")

    _check_semantics(
        label,
        device_class if device_class in _DEVICE_CLASSES else None,
        state_class if state_class in _STATE_CLASSES else None,
        unit if unit in {None, *_CLASS_UNITS.values(), "%"} else None,
        value_map,
        errors,
    )
    if raw_min is not None and raw_max is not None and raw_min > raw_max:
        errors.append(f"{label}.raw_min cannot be greater than raw_max")
    if address is not None and width is not None:
        if address < 0 or address > 65535 or address + width > 65536:
            errors.append(f"{label} address range is invalid")
    if (
        groups is not None
        and isinstance(read_group, str)
        and address is not None
        and width is not None
        and function in _FUNCTIONS
    ):
        group = groups.get(read_group)
        if group is None:
            errors.append(f"{label} references unknown read group {read_group}")
        elif function != group.function:
            errors.append(f"{label} function does not match its read group")
        elif address < group.address or address + width > group.end:
            errors.append(f"{label} falls outside its read group")
    if identity not in (None, "serial", "firmware") or (
        identity in {"serial", "firmware"} and data_type != "ascii"
    ):
        if identity in {"serial", "firmware"}:
            errors.append(f"{label} identity fields must be ascii")

    if (
        entity_key is None
        or name is None
        or address is None
        or function not in _FUNCTIONS
        or width is None
        or data_type not in {*_WIDTH_BY_TYPE, "ascii"}
        or word_order not in _WORD_ORDERS
        or scale is None
        or scale == 0
        or offset is None
        or device_class not in _DEVICE_CLASSES
        or state_class not in _STATE_CLASSES
        or not isinstance(read_group, str)
        or documented_name is None
        or meaning is None
        or source is None
        or verification not in _VERIFICATIONS
        or not isinstance(enabled_default, bool)
        or category not in _CATEGORIES
        or identity not in _IDENTITIES
    ):
        return None
    return RegisterDefinition(
        entity_key=entity_key,
        name=name,
        address=address,
        function=function,
        width=width,
        data_type=data_type,
        word_order=word_order,
        scale=scale,
        offset=offset,
        device_class=device_class,
        state_class=state_class,
        unit=unit if unit in {None, *_CLASS_UNITS.values(), "%"} else None,
        precision=precision if isinstance(precision, int) else None,
        entity_category=category,
        enabled_default=enabled_default,
        read_group=read_group,
        documented_name=documented_name,
        meaning=meaning,
        source=source,
        verification=verification,
        value_map=value_map,
        raw_min=raw_min,
        raw_max=raw_max,
        notes=notes,
        identity=identity,
    )


def _check_semantics(
    label: str,
    device_class: str | None,
    state_class: str | None,
    unit: str | None,
    value_map: dict[int, str] | None,
    errors: list[str],
) -> None:
    if device_class == "enum":
        if not value_map:
            errors.append(f"{label} enum sensors require value_map")
        if unit is not None or state_class is not None:
            errors.append(f"{label} enum sensors cannot have a unit or state class")
    elif value_map is not None:
        errors.append(f"{label} value_map is only valid for enum sensors")
    if device_class in _CLASS_UNITS:
        expected_unit = _CLASS_UNITS[device_class]
        if device_class != "enum" and unit != expected_unit:
            errors.append(f"{label} unit does not match device class {device_class}")
        if device_class == "energy":
            if state_class not in {"total", "total_increasing"}:
                errors.append(f"{label} energy must use total or total_increasing")
        elif device_class == "duration":
            if state_class not in {"measurement", "total", "total_increasing"}:
                errors.append(f"{label} duration must use measurement, total, or total_increasing")
        elif device_class not in {None, "enum"} and state_class != "measurement":
            errors.append(f"{label} state class does not match its device class")
    if unit in _UNIT_CLASSES and device_class not in _UNIT_CLASSES[unit]:
        errors.append(f"{label} device class does not match unit {unit}")
    if unit == "kWh" and state_class == "measurement":
        errors.append(f"{label} cannot use an energy unit for an instantaneous measurement")
    if unit == "W" and state_class in {"total", "total_increasing"}:
        errors.append(f"{label} cannot accumulate a power measurement")


def _parse_value_map(value: Any, label: str, errors: list[str]) -> dict[int, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        errors.append(f"{label}.value_map must be a non-empty object")
        return None
    parsed: dict[int, str] = {}
    seen: set[str] = set()
    for key, label_text in value.items():
        if isinstance(key, bool) or not isinstance(key, str) or not _is_int_text(key):
            errors.append(f"{label}.value_map keys must be decimal strings")
            continue
        if not isinstance(label_text, str) or not label_text.strip():
            errors.append(f"{label}.value_map values must be text")
            continue
        if label_text in seen:
            errors.append(f"{label}.value_map contains duplicate label {label_text}")
        seen.add(label_text)
        parsed[int(key)] = label_text
    return parsed or None


def _reject_overlaps(
    spans: list[tuple[str, str, int, int]],
    kind: str,
    errors: list[str],
) -> None:
    by_function: dict[str, list[tuple[str, int, int]]] = {}
    for function, name, start, end in spans:
        by_function.setdefault(function, []).append((name, start, end))
    for items in by_function.values():
        items.sort(key=lambda item: item[1])
        for previous, current in zip(items, items[1:], strict=False):
            if current[1] < previous[2]:
                errors.append(f"overlapping {kind} {previous[0]} and {current[0]}")


def _reject_unknown(
    payload: dict[str, Any],
    allowed: set[str],
    label: str,
    errors: list[str],
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        errors.append(f"{label} has unknown fields: {', '.join(unknown)}")


def _expect_text(value: Any, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be non-empty text")
        return None
    return value.strip()


def _expect_text_list(value: Any, label: str, errors: list[str]) -> list[str] | None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty list")
        return None
    texts: list[str] = []
    for index, item in enumerate(value):
        text = _expect_text(item, f"{label}[{index}]", errors)
        if text is not None:
            texts.append(text)
    return texts or None


def _expect_id(value: Any, label: str, errors: list[str]) -> str | None:
    text = _expect_text(value, label, errors)
    if text is not None and not _ENTITY_KEY.match(text):
        errors.append(f"{label} must be a lowercase identifier")
        return None
    return text


def _expect_int(value: Any, label: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return None
    return value


def _optional_int(value: Any, label: str, errors: list[str]) -> int | None:
    if value is None:
        return None
    return _expect_int(value, label, errors)


def _expect_number(value: Any, label: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label} must be a number")
        return None
    if not math.isfinite(float(value)):
        errors.append(f"{label} must be finite")
        return None
    return float(value)


def _is_int_text(value: str) -> bool:
    if value in {"", "-", "+"}:
        return False
    body = value[1:] if value[0] in "+-" else value
    return body.isdigit()


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True
