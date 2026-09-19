"""Data update coordinator for Solarman Cloud."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SolarmanApiError, SolarmanAuthError, SolarmanCloudApi
from .const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    DEFAULT_BASE_URL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SolarmanCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch station data from the Solarman cloud on a schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from a config entry."""
        self.entry = entry
        self.station_id: int = int(entry.data[CONF_STATION_ID])
        # Static detail fetched once at setup (capacity, battery, SN, ...).
        self.detail: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []

        session = async_get_clientsession(hass)
        self.api = SolarmanCloudApi(
            session,
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            entry.data.get(CONF_REGION, DEFAULT_REGION),
        )

        scan = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.station_id}",
            update_interval=timedelta(seconds=int(scan)),
        )

    async def async_setup(self) -> None:
        """One-time fetch of static device/detail info."""
        try:
            self.detail = await self.api.async_get_station_detail(self.station_id)
            self.devices = await self.api.async_get_devices(self.station_id)
        except SolarmanApiError as err:
            _LOGGER.warning("Could not load station detail: %s", err)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest live station summary."""
        try:
            return await self.api.async_get_station(self.station_id)
        except SolarmanAuthError as err:
            # Surface as auth failure so HA can trigger reauth.
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except SolarmanApiError as err:
            raise UpdateFailed(f"Error communicating with Solarman: {err}") from err
