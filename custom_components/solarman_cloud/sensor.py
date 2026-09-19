"""Sensor platform for Solarman Cloud."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import token_expiry
from .const import CONF_STATION_NAME, DOMAIN, MANUFACTURER
from .coordinator import SolarmanCoordinator


@dataclass(frozen=True, kw_only=True)
class SolarmanSensorDescription(SensorEntityDescription):
    """Describes a Solarman sensor and how to read its value."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda data: None


def _as_timestamp(data: dict[str, Any]) -> datetime | None:
    ts = data.get("lastUpdateTime")
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


SENSORS: tuple[SolarmanSensorDescription, ...] = (
    SolarmanSensorDescription(
        key="generationPower",
        translation_key="production_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("generationPower"),
    ),
    SolarmanSensorDescription(
        key="usePower",
        translation_key="consumption_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("usePower"),
    ),
    SolarmanSensorDescription(
        key="gridPower",
        translation_key="grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("gridPower"),
    ),
    SolarmanSensorDescription(
        key="buyPower",
        translation_key="buy_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("buyPower"),
    ),
    SolarmanSensorDescription(
        key="generationValue",
        translation_key="production_today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("generationValue"),
    ),
    SolarmanSensorDescription(
        key="generationMonth",
        translation_key="production_month",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("generationMonth"),
    ),
    SolarmanSensorDescription(
        key="generationTotal",
        translation_key="production_total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.get("generationTotal"),
    ),
    SolarmanSensorDescription(
        key="batterySoc",
        translation_key="battery_soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("batterySoc"),
    ),
    SolarmanSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("temperature"),
    ),
    SolarmanSensorDescription(
        key="networkStatus",
        translation_key="network_status",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["ALL_ONLINE", "PARTIAL_OFFLINE", "ALL_OFFLINE"],
        value_fn=lambda d: d.get("networkStatus"),
    ),
    SolarmanSensorDescription(
        key="lastUpdateTime",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_as_timestamp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: SolarmanCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    entities: list[SensorEntity] = [
        SolarmanSensor(coordinator, entry, desc)
        for desc in SENSORS
        # Only create a sensor if the field is present and not null for this station.
        if data.get(desc.key) is not None
    ]
    entities.append(SolarmanTokenSensor(coordinator, entry))
    async_add_entities(entities)


class SolarmanSensor(CoordinatorEntity[SolarmanCoordinator], SensorEntity):
    """A single station-level sensor."""

    _attr_has_entity_name = True
    entity_description: SolarmanSensorDescription

    def __init__(
        self,
        coordinator: SolarmanCoordinator,
        entry: ConfigEntry,
        description: SolarmanSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

        station_name = entry.data.get(CONF_STATION_NAME) or entry.title
        detail = coordinator.detail or {}
        model = None
        if capacity := detail.get("installedCapacity"):
            model = f"{capacity} kW"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.station_id))},
            name=station_name,
            manufacturer=MANUFACTURER,
            model=model,
            configuration_url="https://home.solarmanpv.com",
        )

    @property
    def native_value(self) -> Any:
        """Return the current value read from coordinator data."""
        return self.entity_description.value_fn(self.coordinator.data or {})


class SolarmanTokenSensor(CoordinatorEntity[SolarmanCoordinator], SensorEntity):
    """When the stored refresh token expires.

    The server rotates the token on every renewal; watching this value shows
    whether a rotation also extends the validity window or keeps the original
    deadline, which decides if a token ever has to be supplied by hand again.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "token_expires"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarmanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_token_expires"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.station_id))}
        )

    @property
    def native_value(self) -> datetime | None:
        """Expiry of the refresh token currently held by the client."""
        return token_expiry(self.coordinator.api.refresh_token)
