"""Config and options flow for Solarman Cloud."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolarmanApiError, SolarmanAuthError, SolarmanCloudApi
from .const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class SolarmanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._creds: dict[str, Any] = {}
        self._stations: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: collect credentials and validate them by logging in."""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = SolarmanCloudApi(
                session,
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                user_input[CONF_BASE_URL],
                user_input[CONF_REGION],
            )
            try:
                await api.async_login()
                self._stations = await api.async_get_stations()
            except SolarmanAuthError as err:
                _LOGGER.error("Solarman login rejected: %s", err)
                errors["base"] = "invalid_auth"
            except SolarmanApiError as err:
                _LOGGER.error("Solarman API error during setup: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surface anything else in the log
                _LOGGER.exception("Unexpected error during Solarman login")
                errors["base"] = "unknown"
            else:
                if not self._stations:
                    errors["base"] = "no_stations"
                else:
                    self._creds = user_input
                    return await self.async_step_station()

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_REGION, default=DEFAULT_REGION): str,
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: pick which station to add."""
        choices = {
            str(s.get("id")): s.get("name") or f"Station {s.get('id')}"
            for s in self._stations
        }

        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]
            await self.async_set_unique_id(f"{DOMAIN}_{station_id}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=choices.get(station_id, f"Station {station_id}"),
                data={
                    **self._creds,
                    CONF_STATION_ID: int(station_id),
                    CONF_STATION_NAME: choices.get(station_id),
                },
                options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
            )

        # Single station: skip the picker.
        if len(choices) == 1:
            only = next(iter(choices))
            return await self.async_step_station({CONF_STATION_ID: only})

        schema = vol.Schema({vol.Required(CONF_STATION_ID): vol.In(choices)})
        return self.async_show_form(step_id="station", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SolarmanOptionsFlow()


class SolarmanOptionsFlow(OptionsFlow):
    """Allow changing the polling interval after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
