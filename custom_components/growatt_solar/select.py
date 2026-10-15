"""List settings such as energy priority."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GrowattConfigEntry, GrowattCoordinator
from .register_map import ControlDefinition
from .setting import GrowattSetting, option_label, option_value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrowattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one select for every mapped choice list."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        GrowattSelect(coordinator, control)
        for control in coordinator.model.controls
        if control.platform == "select"
    )


class GrowattSelect(GrowattSetting, SelectEntity):
    """A named choice stored as an integer code."""

    def __init__(self, coordinator: GrowattCoordinator, control: ControlDefinition) -> None:
        """Expose the documented choices in protocol order."""
        super().__init__(coordinator, control)
        self._attr_options = [option.label for option in control.options]

    @property
    def current_option(self) -> str | None:
        """The choice currently stored on the inverter."""
        raw = self._raw()
        if raw is None:
            return None
        return option_label(self.control, raw)

    async def async_select_option(self, option: str) -> None:
        """Write the code for the chosen label."""
        raw = option_value(self.control, option)
        if raw is None:
            raise HomeAssistantError("That choice is not available for this inverter.")
        await self._commit(raw)
