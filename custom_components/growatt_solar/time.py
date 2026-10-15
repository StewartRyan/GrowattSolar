"""Clock settings for charge and grid-first windows."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GrowattConfigEntry
from .setting import GrowattSetting, pack_clock, unpack_clock


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrowattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one time control for every mapped schedule clock."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        GrowattTime(coordinator, control)
        for control in coordinator.model.controls
        if control.platform == "time"
    )


class GrowattTime(GrowattSetting, TimeEntity):
    """A start or end time stored as hour and minute bytes."""

    @property
    def native_value(self) -> time | None:
        """Clock time from the high and low bytes of the register."""
        raw = self._raw()
        if raw is None:
            return None
        return unpack_clock(raw)

    async def async_set_value(self, value: time) -> None:
        """Store the selected hour and minute. Seconds are not kept."""
        await self._commit(pack_clock(value))
