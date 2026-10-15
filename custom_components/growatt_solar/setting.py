"""Shared behavior for inverter configuration entities."""

from __future__ import annotations

from datetime import time

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GrowattCoordinator
from .register_map import ControlDefinition


def pack_clock(value: time) -> int:
    """Store an hour and minute the way the schedule registers do."""
    return (value.hour << 8) | value.minute


def unpack_clock(raw: int) -> time | None:
    """Recover a clock time from a schedule register."""
    hour = (raw >> 8) & 0xFF
    minute = raw & 0xFF
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def option_label(control: ControlDefinition, raw: int) -> str | None:
    """Map a register code to the choice shown in the UI."""
    for option in control.options:
        if option.value == raw:
            return option.label
    return None


def option_value(control: ControlDefinition, label: str) -> int | None:
    """Map a UI choice back to its register code."""
    for option in control.options:
        if option.label == label:
            return option.value
    return None


class GrowattSetting(CoordinatorEntity[GrowattCoordinator]):
    """A configuration entity whose state is one holding register."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowattCoordinator, control: ControlDefinition) -> None:
        """Bind the entity to one mapped setting."""
        super().__init__(coordinator)
        self.control = control
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{control.entity_key}"
        self._attr_translation_key = control.entity_key
        self._attr_icon = control.icon

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the setting to the inverter device."""
        return self.coordinator.device_info()

    @property
    def available(self) -> bool:
        """Unavailable until this register has been read."""
        return super().available and self._raw() is not None

    def _raw(self) -> int | None:
        return self.coordinator.control_words.get(self.control.entity_key)

    async def _commit(self, raw: int) -> None:
        await self.coordinator.async_set_control(self.control.entity_key, raw)
