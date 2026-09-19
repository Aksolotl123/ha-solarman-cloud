"""Client for the (unofficial) SolarmanPV web API.

Authentication note
-------------------
Solarman protects password login (``grant_type=mdc_password``) with a slider
CAPTCHA; a scripted password login is rejected with HTTP 412 ``AUTH_SLIDE_ERROR``.
This client therefore does **not** log in with a password. Instead the user signs
in once themselves in a browser (solving the slider) and supplies the resulting
*refresh token*. Renewal uses ``grant_type=refresh_token``, which needs no CAPTCHA.

The refresh token is rotated on every renewal, so the caller must persist the new
value through ``token_saver`` or access will be lost once the old one expires.

This is a private, undocumented API and may change without notice. The supported
alternative is Solarman's official OpenAPI with an App ID / App Secret.
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from datetime import datetime, timezone
from json import loads as json_loads
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import (
    CLIENT_ID,
    PATH_DEVICE_LIST,
    PATH_STATION_DETAIL,
    PATH_STATION_SEARCH,
    PATH_TOKEN,
    SYSTEM,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def token_expiry(token: str) -> datetime | None:
    """Return a JWT's expiry, or None if it cannot be read.

    Only the ``exp`` claim is used; no other claim is read or logged.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json_loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
    except (IndexError, ValueError, binascii.Error, TypeError):
        return None
    if not exp:
        return None
    return datetime.fromtimestamp(int(exp), tz=timezone.utc)


class SolarmanAuthError(Exception):
    """Raised when the refresh token is rejected and the user must re-supply one."""


class SolarmanApiError(Exception):
    """Raised for other API / transport errors."""


class SolarmanCloudApi:
    """Minimal async client for the SolarmanPV web API, driven by a refresh token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        base_url: str,
        region: str,
        token_saver: Callable[[str], None] | None = None,
    ) -> None:
        """Initialise the client with a user-supplied refresh token."""
        self._session = session
        self._refresh_token = refresh_token
        self._base_url = base_url.rstrip("/")
        self._region = region
        self._token_saver = token_saver

        self._access_token: str | None = None
        self._expires_at: float = 0.0

    @property
    def refresh_token(self) -> str:
        """The current (possibly rotated) refresh token."""
        return self._refresh_token

    # -- auth ---------------------------------------------------------------

    async def async_refresh(self) -> None:
        """Exchange the refresh token for a new access token.

        The server rotates the refresh token, so the new one is stored and handed
        to ``token_saver`` for persistence.
        """
        url = self._base_url + PATH_TOKEN
        form = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLIENT_ID,
            "system": SYSTEM,
            "area": self._region,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            **BROWSER_HEADERS,
        }
        try:
            async with self._session.post(
                url, data=form, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                status = resp.status
                raw = await resp.text()
        except aiohttp.ClientError as err:
            raise SolarmanApiError(f"Token refresh failed: {err}") from err

        try:
            body = json_loads(raw)
        except ValueError:
            body = None

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            # Responses carry no secrets, so they are safe to log.
            _LOGGER.error(
                "Solarman token refresh failed (HTTP %s): %s", status, raw[:400]
            )
            raise SolarmanAuthError(
                f"Refresh token rejected (HTTP {status}). Sign in again in a browser "
                f"and re-enter the token. Server said: {raw[:200]}"
            )

        self._access_token = token
        expires_in = int(body.get("expires_in") or 86400)
        self._expires_at = time.time() + max(expires_in - 300, 60)

        new_refresh = body.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            # Log both expiries to show whether rotation extends the window.
            old_exp = token_expiry(self._refresh_token)
            new_exp = token_expiry(new_refresh)
            _LOGGER.info(
                "Solarman refresh token rotated. Previous expiry: %s, new expiry: %s "
                "(%s). Access token valid until %s.",
                old_exp.isoformat() if old_exp else "unknown",
                new_exp.isoformat() if new_exp else "unknown",
                "window extended"
                if old_exp and new_exp and new_exp > old_exp
                else "window NOT extended - a manual token will be needed before it ends",
                datetime.fromtimestamp(self._expires_at, tz=timezone.utc).isoformat(),
            )
            self._refresh_token = new_refresh
            if self._token_saver is not None:
                # Persist immediately: the previous token may already be void.
                self._token_saver(new_refresh)

    async def _async_ensure_token(self) -> None:
        """Make sure a valid access token is available."""
        if self._access_token is None or time.time() >= self._expires_at:
            await self.async_refresh()

    # -- requests -----------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        """Make an authenticated request, refreshing once on a 401."""
        await self._async_ensure_token()
        result = await self._raw_request(method, path, json=json, params=params)
        if result is _UNAUTHORIZED:
            await self.async_refresh()
            result = await self._raw_request(method, path, json=json, params=params)
            if result is _UNAUTHORIZED:
                raise SolarmanAuthError("Still unauthorized after refreshing the token")
        return result

    async def _raw_request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        url = self._base_url + path
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json;charset=UTF-8",
            **BROWSER_HEADERS,
        }
        try:
            async with self._session.request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    return _UNAUTHORIZED
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise SolarmanApiError(f"Request to {path} failed: {err}") from err
        return body

    # -- high level ---------------------------------------------------------

    async def async_get_stations(self) -> list[dict[str, Any]]:
        """Return the list of stations (plants) with live summary fields."""
        body = await self._request(
            "POST", PATH_STATION_SEARCH, json={"page": 1, "size": 100}
        )
        if not isinstance(body, dict):
            raise SolarmanApiError(f"Unexpected station list response: {body}")
        return body.get("data") or body.get("stationList") or []

    async def async_get_station(self, station_id: int) -> dict[str, Any]:
        """Return the live summary for a single station by id."""
        for station in await self.async_get_stations():
            if station.get("id") == station_id:
                return station
        raise SolarmanApiError(f"Station {station_id} not found")

    async def async_get_station_detail(self, station_id: int) -> dict[str, Any]:
        """Return static detail for a station (capacity, battery, location)."""
        body = await self._request("GET", f"{PATH_STATION_DETAIL}{station_id}")
        if not isinstance(body, dict):
            raise SolarmanApiError(f"Unexpected station detail response: {body}")
        return body

    async def async_get_devices(self, station_id: int) -> list[dict[str, Any]]:
        """Return the devices (inverter, logger, ...) for a station."""
        body = await self._request(
            "POST",
            PATH_DEVICE_LIST,
            json={"stationId": station_id, "page": 1, "size": 100},
        )
        if not isinstance(body, dict):
            return []
        return body.get("data") or []


class _Unauthorized:
    """Sentinel marking a 401 response."""


_UNAUTHORIZED = _Unauthorized()
