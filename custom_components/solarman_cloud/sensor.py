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
from homeassistant.components import webhook as webhook_component
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import token_expiry
from .const import CONF_STATION_NAME, CONF_WEBHOOK_ID, DOMAIN, MANUFACTURER
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
    entities.append(SolarmanLastMonthSensor(coordinator, entry))
    entities.append(SolarmanTokenSensor(coordinator, entry))
    entities.append(SolarmanWebhookSensor(coordinator, entry))
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


class SolarmanLastMonthSensor(CoordinatorEntity[SolarmanCoordinator], SensorEntity):
    """Production over the previous calendar month.

    The live station summary only carries the running month, so the value comes
    from the monthly history the coordinator pulls from the cloud.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "production_last_month"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # No state class on purpose: the value jumps to a different month's total at
    # every month boundary, which the recorder would read as a meter reset. The
    # full series lives in the imported monthly statistic instead.
    _attr_icon = "mdi:calendar-arrow-left"

    def __init__(self, coordinator: SolarmanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_production_last_month"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.station_id))}
        )

    def _last_month(self) -> tuple[int, int]:
        """Return (year, month) of the month before the current local one."""
        now = dt_util.now()
        return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)

    @property
    def native_value(self) -> float | None:
        """Last month's production, or None until the history has been read."""
        return self.coordinator.production_for_month(*self._last_month())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Name the month the value belongs to, which is otherwise implicit."""
        year, month = self._last_month()
        return {"month": f"{year}-{month:02d}"}


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


class SolarmanWebhookSensor(CoordinatorEntity[SolarmanCoordinator], SensorEntity):
    """The address the browser script posts a refreshed token to.

    Shown as an entity so it can be copied from the UI without hunting through
    the log, which is where it would otherwise only appear once at startup.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "token_webhook"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:webhook"

    def __init__(self, coordinator: SolarmanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_token_webhook"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.station_id))}
        )

    def _url(self) -> str | None:
        """Build the externally reachable webhook URL, if there is one."""
        webhook_id = self._entry.data.get(CONF_WEBHOOK_ID)
        if not webhook_id:
            return None
        try:
            # The browser may be off-network, so prefer an external address.
            base = get_url(self.hass, allow_cloud=True, prefer_external=True)
        except NoURLAvailableError:
            base = ""
        return f"{base}{webhook_component.async_generate_path(webhook_id)}"

    @property
    def native_value(self) -> str | None:
        """The URL, or a pointer to the attribute if it is too long for a state."""
        url = self._url()
        if not url:
            return None
        # Home Assistant caps state strings at 255 characters.
        return url if len(url) <= 255 else "see 'url' attribute"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the full URL, which is never truncated here."""
        return {"url": self._url()}
