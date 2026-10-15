"""Constants for the GrowattSolar integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "growatt_solar"

CONF_MODEL: Final = "model_id"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_UNIT_ID: Final = "unit_id"
CONF_ADDRESS_OFFSET: Final = "address_offset"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_PORT: Final = 502
DEFAULT_UNIT_ID: Final = 1
DEFAULT_ADDRESS_OFFSET: Final = 0
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 300
HOLD_SECONDS: Final = 120

PLATFORMS: Final[list[Platform]] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]
