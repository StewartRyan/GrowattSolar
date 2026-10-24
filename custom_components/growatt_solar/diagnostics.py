"""Diagnostics without the inverter serial number."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS_OFFSET, CONF_MODEL, CONF_PORT, CONF_SCAN_INTERVAL, CONF_UNIT_ID
from .coordinator import GrowattConfigEntry
from .register_map import load_catalog

TO_REDACT = {"serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: GrowattConfigEntry,
) -> dict[str, Any]:
    """Return mapping, transport, and reading details for troubleshooting."""
    coordinator = entry.runtime_data
    catalog = await hass.async_add_executor_job(load_catalog)
    readings: dict[str, Any] = {}
    for key, reading in (coordinator.data or {}).items():
        if reading is None:
            readings[key] = None
        else:
            readings[key] = {"value": reading.value, "raw": reading.raw}
    payload: dict[str, Any] = {
        "model_id": entry.data.get(CONF_MODEL),
        "model_name": coordinator.model.model_name,
        "port": entry.data.get(CONF_PORT),
        "unit_id": entry.data.get(CONF_UNIT_ID),
        "address_offset": entry.data.get(CONF_ADDRESS_OFFSET),
        "scan_interval": entry.options.get(
            CONF_SCAN_INTERVAL,
            coordinator.model.connection.default_scan_interval,
        ),
        "verification": coordinator.model.verification_counts(),
        "mapping_issues": [
            {"filename": issue.filename, "message": issue.message} for issue in catalog.issues
        ],
        "last_update_success": coordinator.last_update_success,
        "group_errors": coordinator.group_errors,
        "register_errors": coordinator.register_errors,
        "held_readings": len(coordinator.held_ages),
        "oldest_held_seconds": max(coordinator.held_ages.values(), default=None),
        "readings": readings,
    }
    return async_redact_data(payload, TO_REDACT)
