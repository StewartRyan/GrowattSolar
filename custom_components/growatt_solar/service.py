"""Services for writing a whole schedule window at once."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import GrowattCoordinator

SERVICE_SET_WINDOW = "set_window"

WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required("start_entity"): cv.entity_id,
        vol.Required("end_entity"): cv.entity_id,
        vol.Required("enable_entity"): cv.entity_id,
        vol.Required("start"): cv.time,
        vol.Required("end"): cv.time,
        vol.Required("enabled"): cv.boolean,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the schedule service once for every configured inverter."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_WINDOW):
        return

    async def set_window(call: ServiceCall) -> None:
        start_coordinator, start_key = _bound(hass, call.data["start_entity"])
        end_coordinator, end_key = _bound(hass, call.data["end_entity"])
        enable_coordinator, enable_key = _bound(hass, call.data["enable_entity"])
        if start_coordinator is not end_coordinator or start_coordinator is not enable_coordinator:
            raise HomeAssistantError("Choose the start, end, and enable from the same window.")
        await start_coordinator.async_set_window(
            start_key,
            end_key,
            enable_key,
            call.data["start"],
            call.data["end"],
            call.data["enabled"],
        )

    hass.services.async_register(DOMAIN, SERVICE_SET_WINDOW, set_window, schema=WINDOW_SCHEMA)


def _bound(hass: HomeAssistant, entity_id: str) -> tuple[GrowattCoordinator, str]:
    entity = er.async_get(hass).async_get(entity_id)
    if (
        entity is None
        or entity.platform != DOMAIN
        or not entity.unique_id
        or "_" not in entity.unique_id
    ):
        raise HomeAssistantError(f"{entity_id} is not a GrowattSolar schedule setting.")
    entry_id, key = entity.unique_id.split("_", 1)
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("That Growatt inverter is not available.")
    return entry.runtime_data, key
