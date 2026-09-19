"""Data update coordinator for Solarman Cloud."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SolarmanApiError, SolarmanAuthError, SolarmanCloudApi
from .const import (
    CONF_BASE_URL,
    CONF_REFRESH_TOKEN,
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
        self.detail: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []

        session = async_get_clientsession(hass)
        self.api = SolarmanCloudApi(
            session,
            entry.data[CONF_REFRESH_TOKEN],
            entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            entry.data.get(CONF_REGION, DEFAULT_REGION),
            token_saver=self._save_refresh_token,
        )

        self.scan_interval = int(
            entry.options.get(
                CONF_SCAN_INTERVAL,
                entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {self.station_id}",
            update_interval=timedelta(seconds=self.scan_interval),
        )

    @callback
    def _save_refresh_token(self, token: str) -> None:
        """Persist a rotated refresh token into the config entry.

        The server issues a new refresh token on every renewal and invalidates the
        old one, so this must be stored or the integration stops working after the
        access token expires.
        """
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_REFRESH_TOKEN: token}
        )

    async def async_setup(self) -> None:
        """One-time fetch of static device/detail info."""
        try:
            self.detail = await self.api.async_get_station_detail(self.station_id)
            self.devices = await self.api.async_get_devices(self.station_id)
        except (SolarmanApiError, SolarmanAuthError) as err:
            _LOGGER.warning("Could not load station detail: %s", err)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest live station summary."""
        try:
            return await self.api.async_get_station(self.station_id)
        except SolarmanAuthError as err:
            # Prompts the user to paste a fresh token via the reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SolarmanApiError as err:
            raise UpdateFailed(f"Error communicating with Solarman: {err}") from err
