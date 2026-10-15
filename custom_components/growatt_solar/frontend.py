"""Serve the schedule editor as part of the integration."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STATIC_URL = "/growatt_solar_static"
CARD_FILE = "schedule-card.js"


def _card_token(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:12]


async def async_register_schedule_card(hass: HomeAssistant) -> None:
    """Publish the schedule card and ask the frontend to load it."""
    if hass.http is None or DATA_EXTRA_MODULE_URL not in hass.data:
        return
    directory = Path(__file__).parent / "www"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(directory), False)]
        )
    except RuntimeError:
        _LOGGER.debug("Schedule card is already published")
    token = await hass.async_add_executor_job(_card_token, directory / CARD_FILE)
    add_extra_js_url(hass, f"{STATIC_URL}/{CARD_FILE}?v={token}")
