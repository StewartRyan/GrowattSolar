"""Home Assistant entry point for GrowattSolar."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from . import modbus_client
from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_HOST,
    CONF_MODEL,
    CONF_PORT,
    CONF_UNIT_ID,
    PLATFORMS,
)
from .coordinator import GrowattConfigEntry, GrowattCoordinator
from .register_map import load_catalog

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register services used by the schedule editor."""
    del config
    from .frontend import async_register_schedule_card
    from .service import async_setup_services

    await async_setup_services(hass)
    await async_register_schedule_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GrowattConfigEntry) -> bool:
    """Connect one inverter and start its polling loop."""
    catalog = load_catalog()
    model = catalog.models.get(entry.data[CONF_MODEL])
    if model is None:
        raise ConfigEntryError(f"Inverter mapping {entry.data[CONF_MODEL]!r} is not available")

    client = modbus_client.create_client(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_UNIT_ID],
        address_offset=entry.data[CONF_ADDRESS_OFFSET],
        timeout=model.connection.response_timeout,
        command_interval=model.connection.minimum_command_interval,
    )
    coordinator = GrowattCoordinator(hass, entry, client, model)
    ready = False
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        ready = True
    finally:
        if not ready:
            await coordinator.async_shutdown()
    _LOGGER.debug("Configured %s", coordinator.model.model_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrowattConfigEntry) -> bool:
    """Stop polling and release the socket."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: GrowattConfigEntry) -> None:
    """Reload after options or mapping changes."""
    await hass.config_entries.async_reload(entry.entry_id)
