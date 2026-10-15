"""One polling loop per configured inverter."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import time as clock_time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_SCAN_INTERVAL, DOMAIN, HOLD_SECONDS
from .decoding import DecodedReading, decode_register
from .errors import DeviceReadError, TransportError
from .modbus_client import GrowattModbusClient
from .register_map import ControlDefinition, InverterModel, ReadGroup, RegisterDefinition

_LOGGER = logging.getLogger(__name__)

# Input 0–107 was read as one block on this SPH on 14 October 2026.
# Input 1000–1120 was not answered, so the battery block stays separate.
_LIVE_WINDOWS: dict[str, tuple[tuple[str, int, int], ...]] = {
    "sph5000_tl_bl_up": (("input", 0, 108),),
}


class GrowattCoordinator(DataUpdateCoordinator[dict[str, DecodedReading | None]]):
    """Poll every read group on one shared schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: GrowattModbusClient,
        model: InverterModel,
    ) -> None:
        configured = entry.options.get(CONF_SCAN_INTERVAL, model.connection.default_scan_interval)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=model.model_name,
            update_interval=timedelta(seconds=float(configured)),
        )
        self.entry = entry
        self.client = client
        self.model = model
        self.group_errors: dict[str, str] = {}
        self.register_errors: dict[str, str] = {}
        self.held_ages: dict[str, float] = {}
        self._last_good: dict[str, tuple[DecodedReading, float]] = {}
        self.serial_number: str | None = None
        self.firmware_version: str | None = None
        self.control_words: dict[str, int | None] = {
            control.entity_key: None for control in model.controls
        }
        self._poll_lock = asyncio.Lock()
        self._live_ready = False
        self._settings_due = 0.0

    async def async_set_control(self, entity_key: str, raw: int) -> None:
        """Write one mapped setting and keep it only after the inverter reads back."""
        control = self._control(entity_key)
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 0xFFFF:
            raise HomeAssistantError("That value cannot be stored in one register.")
        async with self._poll_lock:
            try:
                actual = await self._write_control(control, raw)
            except (TransportError, DeviceReadError) as err:
                raise HomeAssistantError("The inverter did not accept that setting.") from err
            if actual != raw:
                kept = _shown(control, actual)
                requested = _shown(control, raw)
                raise HomeAssistantError(f"The inverter kept {kept} instead of {requested}.")
            self.control_words[entity_key] = actual
        self.async_update_listeners()

    async def async_set_window(
        self,
        start_key: str,
        end_key: str,
        enable_key: str,
        start: clock_time,
        end: clock_time,
        enabled: bool,
    ) -> None:
        """Write one schedule window's start, end, and enable in a single step."""
        controls = (self._control(start_key), self._control(end_key), self._control(enable_key))
        raws = (_pack_clock(start), _pack_clock(end), 1 if enabled else 0)
        span = controls[0].write_span
        if span is None or any(control.write_span != span for control in controls):
            raise HomeAssistantError("Those settings are not one schedule window.")
        address, count = span
        if enabled:
            clash = _shared_minute(self, span, raws[0], raws[1])
            if clash is not None:
                raise HomeAssistantError(clash)
        async with self._poll_lock:
            try:
                current = list(await self.client.read_registers("holding", address, count))
                for control, raw in zip(controls, raws, strict=True):
                    current[control.address - address] = raw
                await self.client.write_registers(address, current)
                confirmed = list(await self.client.read_registers("holding", address, count))
            except (TransportError, DeviceReadError) as err:
                raise HomeAssistantError(
                    f"The inverter did not accept that schedule. {err}"
                ) from err
            for control, raw in zip(controls, raws, strict=True):
                actual = confirmed[control.address - address]
                if actual != raw:
                    kept = _shown(control, actual)
                    requested = _shown(control, raw)
                    raise HomeAssistantError(f"The inverter kept {kept} instead of {requested}.")
            for other in self.model.controls:
                if other.write_span == span:
                    self.control_words[other.entity_key] = confirmed[other.address - address]
        self.async_update_listeners()

    async def _write_control(self, control: ControlDefinition, raw: int) -> int:
        if control.write_span is None:
            await self.client.write_register(control.address, raw)
            return (await self.client.read_registers("holding", control.address, 1))[0]
        start, count = control.write_span
        current = list(await self.client.read_registers("holding", start, count))
        current[control.address - start] = raw
        await self.client.write_registers(start, current)
        actual = (await self.client.read_registers("holding", control.address, 1))[0]
        if actual == raw:
            for other in self.model.controls:
                if other.write_span == control.write_span:
                    self.control_words[other.entity_key] = current[other.address - start]
        return actual

    def _control(self, entity_key: str) -> ControlDefinition:
        for control in self.model.controls:
            if control.entity_key == entity_key:
                return control
        raise HomeAssistantError("That setting is not available on this inverter.")

    async def _async_update_data(self) -> dict[str, DecodedReading | None]:
        async with self._poll_lock:
            return await self._poll()

    async def async_shutdown(self) -> None:
        """Stop polling and close the socket."""
        await super().async_shutdown()
        await self.client.close()

    def device_info(self) -> DeviceInfo:
        """Device registry payload for every entity of this inverter."""
        info: DeviceInfo = {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "manufacturer": self.model.manufacturer,
            "model": self.model.model_name,
            "name": f"{self.model.manufacturer} {self.model.model_name}",
        }
        if self.serial_number:
            info["serial_number"] = self.serial_number
        if self.firmware_version:
            info["sw_version"] = self.firmware_version
        return info

    async def _poll(self) -> dict[str, DecodedReading | None]:
        readings: dict[str, DecodedReading | None] = {
            register.entity_key: None for register in self.model.registers
        }
        self.group_errors = {}
        self.register_errors = {}
        self.held_ages = {}
        members = _registers_by_group(self.model.registers)
        transport_lost = False
        required_ok = False
        covered: set[str] = set()

        for function, address, count in _LIVE_WINDOWS.get(self.model.model_id, ()):
            groups = [
                group
                for group in self.model.read_groups.values()
                if _inside(group, function, address, count)
            ]
            if not groups:
                continue
            try:
                words = await self.client.read_registers(function, address, count)
            except (TransportError, DeviceReadError) as err:
                _LOGGER.debug("Combined read at %s failed: %s", address, err)
                for group in groups:
                    if await self._read_group(group, members, readings):
                        required_ok = required_ok or not group.optional
                    if self.group_errors.get(group.group_id) == "transport":
                        transport_lost = True
                covered.update(group.group_id for group in groups)
                continue
            for group in groups:
                offset = group.address - address
                self._accept(group, words[offset : offset + group.count], members, readings)
                if not group.optional:
                    required_ok = True
            covered.update(group.group_id for group in groups)

        pending = [
            group
            for group in self.model.read_groups.values()
            if group.group_id not in covered
        ]
        required = [group for group in pending if not group.optional]
        now = time.monotonic()
        if not self._live_ready or now >= self._settings_due:
            optional = [group for group in pending if group.optional]
            self._live_ready = True
            self._settings_due = now + 30
        else:
            optional = []
        pending = required + optional

        for group in self.model.read_groups.values():
            if group.group_id in covered or group in pending:
                continue
            self._hold(members.get(group.group_id, []), readings)

        for group in pending:
            if transport_lost:
                self.group_errors[group.group_id] = "skipped_after_transport_error"
                self._hold(members.get(group.group_id, []), readings)
                continue
            if await self._read_group(group, members, readings):
                required_ok = required_ok or not group.optional
            if self.group_errors.get(group.group_id) == "transport":
                transport_lost = True

        if not required_ok and not self.held_ages:
            summary = ", ".join(f"{group}={kind}" for group, kind in self.group_errors.items())
            raise UpdateFailed(f"no required register group could be read ({summary})")
        _LOGGER.debug(
            "Read %s (%s group problems, %s decode problems)",
            self.entry.data.get(CONF_HOST, "inverter"),
            len(self.group_errors),
            len(self.register_errors),
        )
        return readings

    async def _read_group(
        self,
        group: ReadGroup,
        members: dict[str, list[RegisterDefinition]],
        readings: dict[str, DecodedReading | None],
    ) -> bool:
        """Read one group. Return whether the inverter answered."""
        grouped = members.get(group.group_id, [])
        try:
            words = await self.client.read_registers(group.function, group.address, group.count)
        except TransportError as err:
            self.group_errors[group.group_id] = "transport"
            _LOGGER.debug("Transport failure on %s: %s", group.group_id, err)
            self._hold(grouped, readings)
            return False
        except DeviceReadError as err:
            self.group_errors[group.group_id] = "device"
            _LOGGER.debug("Inverter rejected %s: %s", group.group_id, err)
            self._hold(grouped, readings)
            return False
        self._accept(group, words, members, readings)
        return True

    def _accept(
        self,
        group: ReadGroup,
        words: list[int],
        members: dict[str, list[RegisterDefinition]],
        readings: dict[str, DecodedReading | None],
    ) -> None:
        read_at = time.monotonic()
        for register in members.get(group.group_id, []):
            reading, reason = decode_register(register, words, group.address)
            readings[register.entity_key] = reading
            if reading is None:
                self._last_good.pop(register.entity_key, None)
            else:
                self._last_good[register.entity_key] = (reading, read_at)
            if reason is not None:
                self.register_errors[register.entity_key] = reason
        self._capture_controls(group, words)
        self._capture_identity(readings)

    def _hold(
        self,
        registers: list[RegisterDefinition],
        readings: dict[str, DecodedReading | None],
    ) -> None:
        now = time.monotonic()
        for register in registers:
            held = self._last_good.get(register.entity_key)
            if held is None:
                continue
            reading, read_at = held
            age = now - read_at
            if age >= HOLD_SECONDS:
                del self._last_good[register.entity_key]
                continue
            readings[register.entity_key] = reading
            self.held_ages[register.entity_key] = round(age, 1)

    def _capture_controls(self, group: ReadGroup, words: list[int]) -> None:
        for control in self.model.controls:
            if control.read_group != group.group_id:
                continue
            self.control_words[control.entity_key] = words[control.address - group.address]

    def _capture_identity(self, readings: dict[str, DecodedReading | None]) -> None:
        for register in self.model.registers:
            reading = readings.get(register.entity_key)
            if reading is None or not isinstance(reading.value, str) or not reading.value:
                continue
            if register.identity == "serial":
                self.serial_number = reading.value
            elif register.identity == "firmware":
                self.firmware_version = reading.value


def _inside(group: ReadGroup, function: str, address: int, count: int) -> bool:
    return group.function == function and address <= group.address and group.end <= address + count


def _pack_clock(value: clock_time) -> int:
    return (value.hour << 8) | value.minute


def _shared_minute(
    coordinator: GrowattCoordinator,
    span: tuple[int, int],
    start_raw: int,
    end_raw: int,
) -> str | None:
    """Return an explanation when another enabled window already uses a minute."""
    start = _clock_minutes(start_raw)
    end = _clock_minutes(end_raw)
    if start is None or end is None:
        return None
    proposed = _covered_minutes(start, end)
    if not proposed:
        return None
    for enable, other_start, other_end in _schedule_windows(coordinator.model):
        if enable.write_span == span or coordinator.control_words.get(enable.entity_key) != 1:
            continue
        raw_start = coordinator.control_words.get(other_start.entity_key)
        raw_end = coordinator.control_words.get(other_end.entity_key)
        if not isinstance(raw_start, int) or not isinstance(raw_end, int):
            continue
        other_from = _clock_minutes(raw_start)
        other_to = _clock_minutes(raw_end)
        if other_from is None or other_to is None:
            continue
        shared = proposed & _covered_minutes(other_from, other_to)
        if not shared:
            continue
        minute = _minute_label(min(shared))
        taken = f"{_minute_label(other_from)}–{_minute_label(other_to)}"
        return (
            f"{minute} is already used by {enable.name} ({taken}). "
            "The inverter will not let two windows share a minute."
        )
    return None


def _schedule_windows(
    model: InverterModel,
) -> list[tuple[ControlDefinition, ControlDefinition, ControlDefinition]]:
    grouped: dict[tuple[int, int], dict[int, ControlDefinition]] = {}
    for control in model.controls:
        if control.write_span is None:
            continue
        grouped.setdefault(control.write_span, {})[control.address] = control
    windows: list[tuple[ControlDefinition, ControlDefinition, ControlDefinition]] = []
    for (address, count), by_address in grouped.items():
        enable = by_address.get(address + 2)
        start = by_address.get(address)
        end = by_address.get(address + 1)
        if count == 3 and enable is not None and start is not None and end is not None:
            windows.append((enable, start, end))
    return windows


def _clock_minutes(raw: int) -> int | None:
    hour = (raw >> 8) & 0xFF
    minute = raw & 0xFF
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _covered_minutes(start: int, end: int) -> set[int]:
    if start == end:
        return set()
    if start < end:
        return set(range(start, end + 1))
    return set(range(start, 24 * 60)) | set(range(0, end + 1))


def _minute_label(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _shown(control: ControlDefinition, raw: int) -> str:
    if control.platform == "time":
        hour = (raw >> 8) & 0xFF
        minute = raw & 0xFF
        if hour <= 23 and minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    if control.platform == "switch" and raw in (0, 1):
        return "on" if raw else "off"
    if control.platform == "select":
        for option in control.options:
            if option.value == raw:
                return option.label
    return str(raw)


type GrowattConfigEntry = ConfigEntry[GrowattCoordinator]


def _registers_by_group(
    registers: tuple[RegisterDefinition, ...],
) -> dict[str, list[RegisterDefinition]]:
    grouped: dict[str, list[RegisterDefinition]] = {}
    for register in registers:
        grouped.setdefault(register.read_group, []).append(register)
    return grouped
