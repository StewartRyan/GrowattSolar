"""Sensors created from the selected register map."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GrowattConfigEntry, GrowattCoordinator
from .register_map import RegisterDefinition

_DEVICE_CLASSES = {
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "power": SensorDeviceClass.POWER,
    "apparent_power": SensorDeviceClass.APPARENT_POWER,
    "frequency": SensorDeviceClass.FREQUENCY,
    "energy": SensorDeviceClass.ENERGY,
    "battery": SensorDeviceClass.BATTERY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "duration": SensorDeviceClass.DURATION,
    "enum": SensorDeviceClass.ENUM,
}
_STATE_CLASSES = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total": SensorStateClass.TOTAL,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}
_UNITS = {
    "V": UnitOfElectricPotential.VOLT,
    "A": UnitOfElectricCurrent.AMPERE,
    "W": UnitOfPower.WATT,
    "VA": UnitOfApparentPower.VOLT_AMPERE,
    "Hz": UnitOfFrequency.HERTZ,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "%": PERCENTAGE,
    "°C": UnitOfTemperature.CELSIUS,
    "s": UnitOfTime.SECONDS,
}
_CATEGORIES = {
    "diagnostic": EntityCategory.DIAGNOSTIC,
    "config": EntityCategory.CONFIG,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrowattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one sensor for every register in the selected mapping."""
    coordinator = entry.runtime_data
    async_add_entities(
        GrowattModbusSensor(coordinator, register) for register in coordinator.model.registers
    )


class GrowattModbusSensor(CoordinatorEntity[GrowattCoordinator], SensorEntity):
    """A single mapped measurement."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GrowattCoordinator, register: RegisterDefinition) -> None:
        """Bind the sensor to one register definition."""
        super().__init__(coordinator)
        self._register = register
        self.entity_description = _description(register)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{register.entity_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Keep the device attached to the latest identity readings."""
        return self.coordinator.device_info()

    @property
    def available(self) -> bool:
        """Unavailable when this register's latest read produced no value."""
        if not super().available:
            return False
        return self.coordinator.data.get(self._register.entity_key) is not None

    @property
    def native_value(self) -> float | str | None:
        """Scaled or enumerated value from the last successful decode."""
        reading = self.coordinator.data.get(self._register.entity_key)
        if reading is None:
            return None
        return reading.value


def _description(register: RegisterDefinition) -> SensorEntityDescription:
    options = list(register.value_map.values()) if register.value_map else None
    return SensorEntityDescription(
        key=register.entity_key,
        name=register.name,
        translation_key=register.entity_key,
        device_class=_DEVICE_CLASSES.get(register.device_class) if register.device_class else None,
        state_class=_STATE_CLASSES.get(register.state_class) if register.state_class else None,
        native_unit_of_measurement=_UNITS.get(register.unit) if register.unit else None,
        suggested_display_precision=register.precision,
        entity_category=_CATEGORIES.get(register.entity_category)
        if register.entity_category
        else None,
        entity_registry_enabled_default=register.enabled_default,
        options=options,
    )
