"""Client for the (unofficial) SolarmanPV web API.

This talks to the same backend that the SolarmanPV web portal / mobile app use,
authenticating with the account e-mail + password (``grant_type=mdc_password``),
so no App ID / App Secret is required.

Note: this is a private, undocumented API. It may change without notice and is
likely outside Solarman's official terms of use. For a supported path, request an
App ID / App Secret from Solarman and use their official OpenAPI instead.
"""
from __future__ import annotations

import hashlib
import logging
import time
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

# identity_type: 1 = phone, 2 = email, 3 = username (from the web app).
IDENTITY_EMAIL = 2

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class SolarmanAuthError(Exception):
    """Raised when authentication fails (bad credentials)."""


class SolarmanApiError(Exception):
    """Raised for other API / transport errors."""


class SolarmanCloudApi:
    """Minimal async client for the SolarmanPV web API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        base_url: str,
        region: str,
    ) -> None:
        """Initialise the client. ``password`` is the plain account password."""
        self._session = session
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._region = region

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        # Absolute epoch seconds when the access token expires (0 = unknown/expired).
        self._expires_at: float = 0.0

    # -- auth ---------------------------------------------------------------

    @staticmethod
    def _hash_password(password: str) -> str:
        """SHA-256 hex digest, matching the web app's ``password`` field."""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    async def async_login(self) -> None:
        """Obtain a fresh access + refresh token using the account password."""
        data = {
            "grant_type": "mdc_password",
            "username": self._email,
            "password": self._hash_password(self._password),
            "clear_text_pwd": self._password,
            "identity_type": IDENTITY_EMAIL,
            "client_id": CLIENT_ID,
            "system": SYSTEM,
            "area": self._region,
        }
        await self._token_request(data)

    async def _async_refresh(self) -> None:
        """Renew the access token with the refresh token, falling back to login."""
        if not self._refresh_token:
            await self.async_login()
            return
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLIENT_ID,
            "system": SYSTEM,
            "area": self._region,
        }
        try:
            await self._token_request(data)
        except SolarmanAuthError:
            # Refresh token no longer valid -> full re-login.
            self._refresh_token = None
            await self.async_login()

    async def _token_request(self, data: dict[str, Any]) -> None:
        """POST to the token endpoint and store the resulting tokens."""
        url = self._base_url + PATH_TOKEN
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            async with self._session.post(
                url, data=data, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise SolarmanApiError(f"Token request failed: {err}") from err

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            error = body.get("error") if isinstance(body, dict) else body
            # 'invalid_grant' / bad username or password -> credentials problem.
            if error in ("invalid_grant", "unauthorized") or "PASSWORD" in str(error):
                raise SolarmanAuthError(f"Authentication failed: {error}")
            raise SolarmanApiError(f"No access token in response: {body}")

        self._access_token = token
        if body.get("refresh_token"):
            self._refresh_token = body["refresh_token"]
        # expires_in is in seconds; refresh 5 min early. Default 1 day if absent.
        expires_in = int(body.get("expires_in") or 86400)
        self._expires_at = time.time() + max(expires_in - 300, 60)

    async def _async_ensure_token(self) -> None:
        """Make sure we hold a valid, non-expired access token."""
        if self._access_token is None:
            await self.async_login()
        elif time.time() >= self._expires_at:
            await self._async_refresh()

    # -- requests -----------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        """Make an authenticated request, refreshing the token once on 401."""
        await self._async_ensure_token()
        result = await self._raw_request(method, path, json=json, params=params)
        if result is _UNAUTHORIZED:
            # Token rejected mid-life; refresh and retry exactly once.
            await self._async_refresh()
            result = await self._raw_request(method, path, json=json, params=params)
            if result is _UNAUTHORIZED:
                raise SolarmanAuthError("Still unauthorized after token refresh")
        return result

    async def _raw_request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        url = self._base_url + path
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json;charset=UTF-8",
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
        stations = body.get("data") or body.get("stationList") or []
        return stations

    async def async_get_station(self, station_id: int) -> dict[str, Any]:
        """Return the live summary for a single station by id."""
        stations = await self.async_get_stations()
        for station in stations:
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
