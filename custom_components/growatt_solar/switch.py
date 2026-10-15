"""On/off settings such as a schedule window or grid charging."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GrowattConfigEntry
from .setting import GrowattSetting


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrowattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one switch for every mapped enable register."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        GrowattSwitch(coordinator, control)
        for control in coordinator.model.controls
        if control.platform == "switch"
    )


class GrowattSwitch(GrowattSetting, SwitchEntity):
    """An enable flag stored as 0 or 1."""

    @property
    def is_on(self) -> bool | None:
        """True when the register is 1 and false when it is 0."""
        raw = self._raw()
        if raw == 1:
            return True
        if raw == 0:
            return False
        return None

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable the setting."""
        del kwargs
        await self._commit(1)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable the setting."""
        del kwargs
        await self._commit(0)
