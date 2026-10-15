"""Percentage settings such as a charge limit or stop level."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GrowattConfigEntry, GrowattCoordinator
from .register_map import ControlDefinition
from .setting import GrowattSetting


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrowattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one number for every mapped percentage setting."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        GrowattNumber(coordinator, control)
        for control in coordinator.model.controls
        if control.platform == "number"
    )


class GrowattNumber(GrowattSetting, NumberEntity):
    """A whole-number percentage stored directly in the register."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: GrowattCoordinator, control: ControlDefinition) -> None:
        """Use the documented minimum and maximum as the slider ends."""
        super().__init__(coordinator, control)
        assert control.minimum is not None and control.maximum is not None
        self._attr_native_min_value = float(control.minimum)
        self._attr_native_max_value = float(control.maximum)

    @property
    def native_value(self) -> float | None:
        """The percentage stored on the inverter."""
        raw = self._raw()
        if raw is None or self.control.minimum is None or self.control.maximum is None:
            return None
        if not self.control.minimum <= raw <= self.control.maximum:
            return None
        return float(raw)

    async def async_set_native_value(self, value: float) -> None:
        """Write a whole percentage inside the documented range."""
        fractional = isinstance(value, float) and not value.is_integer()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or fractional:
            raise HomeAssistantError("Enter a whole percentage.")
        raw = int(value)
        if self.control.minimum is None or self.control.maximum is None:
            raise HomeAssistantError("That setting has no allowed range.")
        if not self.control.minimum <= raw <= self.control.maximum:
            raise HomeAssistantError("That percentage is outside the allowed range.")
        await self._commit(raw)
