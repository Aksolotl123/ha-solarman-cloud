"""Config and options flow for Solarman Cloud."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
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
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SolarmanApiError, SolarmanAuthError, SolarmanCloudApi
from .const import (
    AUTH_MODE_OPENAPI,
    AUTH_MODE_PORTAL,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_AUTH_MODE,
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_OPENAPI_URL,
    CONF_PASSWORD,
    CONF_PASSWORD_HASH,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_OPENAPI_URL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .coordinator import auth_mode, build_openapi

_LOGGER = logging.getLogger(__name__)

_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _async_validate(
    hass, token: str, base_url: str, region: str
) -> tuple[str, list[dict[str, Any]]]:
    """Check a refresh token and return the token to store plus its stations.

    Validating spends the token: the server rotates it and hands back a new one.
    The rotated value is returned so the caller stores that rather than the one
    the user pasted, which the server may already consider spent.
    """
    api = SolarmanCloudApi(
        async_get_clientsession(hass), token.strip(), base_url, region
    )
    await api.async_refresh()
    stations = await api.async_get_stations()
    return api.refresh_token, stations


def _openapi_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the official API form into entry data, keeping no plain password.

    The API only ever wants the lowercase SHA-256 digest of the password, so
    that digest is all that gets stored.
    """
    return {
        CONF_AUTH_MODE: AUTH_MODE_OPENAPI,
        CONF_APP_ID: user_input[CONF_APP_ID].strip(),
        CONF_APP_SECRET: user_input[CONF_APP_SECRET].strip(),
        CONF_EMAIL: user_input[CONF_EMAIL].strip(),
        CONF_PASSWORD_HASH: hashlib.sha256(
            user_input[CONF_PASSWORD].encode()
        ).hexdigest(),
        CONF_OPENAPI_URL: user_input[CONF_OPENAPI_URL].strip().rstrip("/"),
    }


def _openapi_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Form for the official API; secrets are never pre-filled."""
    return vol.Schema(
        {
            vol.Required(CONF_APP_ID, default=defaults.get(CONF_APP_ID, "")): str,
            vol.Required(CONF_APP_SECRET): _SECRET,
            vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
            vol.Required(CONF_PASSWORD): _SECRET,
            vol.Required(
                CONF_OPENAPI_URL,
                default=defaults.get(CONF_OPENAPI_URL, DEFAULT_OPENAPI_URL),
            ): str,
        }
    )


class SolarmanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI configuration flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._stations: list[dict[str, Any]] = []

    async def _async_check_openapi(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, Any] | None:
        """Sign in with the official API; return entry data, or None on error."""
        data = _openapi_data(user_input)
        try:
            api = build_openapi(self.hass, data)
            await api.async_login()
            self._stations = await api.async_get_stations()
        except SolarmanAuthError as err:
            _LOGGER.error("Solarman OpenAPI sign-in rejected: %s", err)
            errors["base"] = "invalid_auth"
        except SolarmanApiError as err:
            _LOGGER.error("Solarman OpenAPI error during setup: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001 - never fail silently
            _LOGGER.exception("Unexpected error signing in to the Solarman OpenAPI")
            errors["base"] = "unknown"
        else:
            if not self._stations:
                errors["base"] = "no_stations"
            else:
                return data
        return None

    def _station_missing(self, entry: ConfigEntry) -> bool:
        """Whether the entry's station is absent from the freshly read list."""
        station_id = entry.data.get(CONF_STATION_ID)
        return all(s.get("id") != station_id for s in self._stations)

    # -- new entry ----------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: choose how to sign in."""
        return self.async_show_menu(
            step_id="user", menu_options=["openapi", "portal"]
        )

    async def async_step_openapi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with the official API (App ID + App Secret)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = await self._async_check_openapi(user_input, errors)
            if data is not None:
                self._config = data
                return await self.async_step_station()

        return self.async_show_form(
            step_id="openapi",
            data_schema=_openapi_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with a refresh token copied from the web portal."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, self._stations = await _async_validate(
                    self.hass,
                    user_input[CONF_REFRESH_TOKEN],
                    user_input[CONF_BASE_URL],
                    user_input[CONF_REGION],
                )
            except SolarmanAuthError as err:
                _LOGGER.error("Solarman token rejected: %s", err)
                errors["base"] = "invalid_token"
            except SolarmanApiError as err:
                _LOGGER.error("Solarman API error during setup: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - never fail silently
                _LOGGER.exception("Unexpected error validating Solarman token")
                errors["base"] = "unknown"
            else:
                if not self._stations:
                    errors["base"] = "no_stations"
                else:
                    # Keep the rotated token, not the one the user pasted.
                    self._config = {
                        **user_input,
                        CONF_AUTH_MODE: AUTH_MODE_PORTAL,
                        CONF_REFRESH_TOKEN: token,
                    }
                    return await self.async_step_station()

        schema = vol.Schema(
            {
                vol.Required(CONF_REFRESH_TOKEN): str,
                vol.Required(CONF_REGION, default=DEFAULT_REGION): str,
                vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
            }
        )
        return self.async_show_form(
            step_id="portal", data_schema=schema, errors=errors
        )

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which station to add."""
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
                    **self._config,
                    CONF_STATION_ID: int(station_id),
                    CONF_STATION_NAME: choices.get(station_id),
                },
                options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
            )

        if len(choices) == 1:
            return await self.async_step_station(
                {CONF_STATION_ID: next(iter(choices))}
            )

        schema = vol.Schema({vol.Required(CONF_STATION_ID): vol.In(choices)})
        return self.async_show_form(step_id="station", data_schema=schema)

    # -- switching an existing entry ----------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Switch an existing station between the two ways of signing in.

        The entry, its entities and their history stay as they are; only the
        credentials change. Data of the other mode is kept, so switching back
        does not start from nothing.
        """
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_openapi", "reconfigure_portal"],
        )

    async def async_step_reconfigure_openapi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move an existing entry onto the official API."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = await self._async_check_openapi(user_input, errors)
            if data is not None:
                if self._station_missing(entry):
                    errors["base"] = "station_not_found"
                else:
                    return self.async_update_and_abort(
                        entry, data={**entry.data, **data}
                    )

        return self.async_show_form(
            step_id="reconfigure_openapi",
            data_schema=_openapi_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_reconfigure_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move an existing entry back onto the portal token.

        The field may be left empty to reuse the token still stored from before.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = (user_input.get(CONF_REFRESH_TOKEN) or "").strip() or entry.data.get(
                CONF_REFRESH_TOKEN, ""
            )
            if not token:
                errors["base"] = "invalid_token"
            else:
                try:
                    token, self._stations = await _async_validate(
                        self.hass,
                        token,
                        entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                        entry.data.get(CONF_REGION, DEFAULT_REGION),
                    )
                except SolarmanAuthError:
                    errors["base"] = "invalid_token"
                except SolarmanApiError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error validating Solarman token")
                    errors["base"] = "unknown"
                else:
                    if self._station_missing(entry):
                        errors["base"] = "station_not_found"
                    else:
                        return self.async_update_and_abort(
                            entry,
                            data={
                                **entry.data,
                                CONF_AUTH_MODE: AUTH_MODE_PORTAL,
                                CONF_REFRESH_TOKEN: token,
                            },
                        )

        return self.async_show_form(
            step_id="reconfigure_portal",
            data_schema=vol.Schema({vol.Optional(CONF_REFRESH_TOKEN): str}),
            errors=errors,
        )

    # -- reauth ---------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when the stored credentials stop working."""
        if auth_mode(entry_data) == AUTH_MODE_OPENAPI:
            return await self.async_step_reauth_openapi()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_openapi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask again for the official API credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = await self._async_check_openapi(user_input, errors)
            if data is not None:
                return self.async_update_and_abort(
                    entry, data={**entry.data, **data}
                )

        return self.async_show_form(
            step_id="reauth_openapi",
            data_schema=_openapi_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user for a freshly copied refresh token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                token, _stations = await _async_validate(
                    self.hass,
                    user_input[CONF_REFRESH_TOKEN],
                    entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                    entry.data.get(CONF_REGION, DEFAULT_REGION),
                )
            except SolarmanAuthError:
                errors["base"] = "invalid_token"
            except SolarmanApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Solarman token")
                errors["base"] = "unknown"
            else:
                return self.async_update_and_abort(
                    entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str}),
            errors=errors,
        )

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
