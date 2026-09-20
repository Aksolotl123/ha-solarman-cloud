"""Data update coordinator for Solarman Cloud."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SolarmanApiError, SolarmanAuthError, SolarmanCloudApi
from .const import (
    CONF_BASE_URL,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MONTHLY_HISTORY_LIMIT,
)
from .energy_statistics import async_import_monthly_production

_LOGGER = logging.getLogger(__name__)


class SolarmanCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch station data from the Solarman cloud on a schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from a config entry."""
        self.entry = entry
        self.station_id: int = int(entry.data[CONF_STATION_ID])
        self.detail: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []
        # Production per calendar month, {(year, month): kWh}, straight from the
        # cloud - it reaches back to before Home Assistant knew this station.
        self.monthly: dict[tuple[int, int], float] = {}
        # Production per day, {(year, month, day): kWh}, only for the months that
        # yesterday and today fall in - that is all any report has needed so far.
        self.daily: dict[tuple[int, int, int], float] = {}
        self._history_years: set[int] = set()

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

    def production_for_month(self, year: int, month: int) -> float | None:
        """Production of one calendar month, or None if the cloud has no record."""
        return self.monthly.get((year, month))

    def production_for_day(self, day: date) -> float | None:
        """Production of one calendar day, or None if the cloud has no record."""
        return self.daily.get((day.year, day.month, day.day))

    def monthly_history(self, limit: int = MONTHLY_HISTORY_LIMIT) -> dict[str, float]:
        """Return the most recent months as ``{"YYYY-MM": kWh}``, oldest first.

        Templates cannot reach long-term statistics, so this is how the history
        behind the monthly chart is made available to notifications and cards.
        The list is capped because it is published as a state attribute, which
        the recorder stores on every write.
        """
        ordered = sorted(self.monthly.items())[-limit:]
        return {f"{year}-{month:02d}": value for (year, month), value in ordered}

    async def _async_update_history(self) -> None:
        """Refresh the monthly history and hand it to long-term statistics.

        The first run pulls every year the station has data for; later runs only
        re-read the current year, since finished years no longer change. History
        is a nice-to-have next to the live values, so a failure here is logged
        and swallowed rather than failing the whole update.
        """
        now = dt_util.now()
        try:
            if self._history_years:
                # In early January the previous year may still be settling.
                years = {now.year} | ({now.year - 1} if now.month == 1 else set())
            else:
                years = set(
                    await self.api.async_get_yearly_production(self.station_id)
                ) or {now.year}
            for year in sorted(years):
                months = await self.api.async_get_monthly_production(
                    self.station_id, year
                )
                for month, value in months.items():
                    self.monthly[(year, month)] = value
                self._history_years.add(year)
        except (SolarmanApiError, SolarmanAuthError) as err:
            _LOGGER.warning("Could not load production history: %s", err)
            return

        async_import_monthly_production(
            self.hass,
            self.station_id,
            self.entry.data.get(CONF_STATION_NAME) or self.entry.title,
            self.monthly,
        )

        # Yesterday's total is not in the live summary, so the per-day breakdown
        # is read as well. Only the month holding today, plus the one holding
        # yesterday on the first of a month, is worth asking for.
        today = now.date()
        wanted = {(today.year, today.month)}
        yesterday = today - timedelta(days=1)
        wanted.add((yesterday.year, yesterday.month))
        try:
            days: dict[tuple[int, int, int], float] = {}
            for year, month in sorted(wanted):
                for day, value in (
                    await self.api.async_get_daily_production(
                        self.station_id, year, month
                    )
                ).items():
                    days[(year, month, day)] = value
        except (SolarmanApiError, SolarmanAuthError) as err:
            _LOGGER.warning("Could not load daily production: %s", err)
            return
        # Replace rather than merge, so days the cloud has corrected do not linger.
        self.daily = days

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest live station summary and refresh the history."""
        try:
            data = await self.api.async_get_station(self.station_id)
        except SolarmanAuthError as err:
            # Prompts the user to paste a fresh token via the reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SolarmanApiError as err:
            raise UpdateFailed(f"Error communicating with Solarman: {err}") from err

        await self._async_update_history()
        return data
