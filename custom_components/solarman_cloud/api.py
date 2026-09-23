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
alternative is Solarman's official OpenAPI with an App ID / App Secret, which
``SolarmanOpenApi`` below speaks. Both clients offer the same high-level methods
and return the station summary under the portal's field names, so the
coordinator and sensors do not care which one is in use.
"""
from __future__ import annotations

import base64
import binascii
import calendar
import logging
import time
from datetime import date, datetime, timezone
from json import loads as json_loads
from collections.abc import Callable
from typing import Any

import aiohttp

from homeassistant.util import dt as dt_util

from .const import (
    CLIENT_ID,
    OPENAPI_PATH_STATION_DEVICES,
    OPENAPI_PATH_STATION_HISTORY,
    OPENAPI_PATH_STATION_LIST,
    OPENAPI_PATH_STATION_REALTIME,
    OPENAPI_PATH_TOKEN,
    PATH_DEVICE_LIST,
    PATH_HISTORY_STATS,
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

    def set_refresh_token(self, token: str) -> None:
        """Adopt a token obtained elsewhere, discarding the cached access token."""
        self._refresh_token = token
        self._access_token = None
        self._expires_at = 0.0

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

    async def _async_history_totals(
        self, station_id: int, scope: str, field: str, params: dict[str, str] | None = None
    ) -> dict[int, float]:
        """Return ``{field value: kWh}`` from a production history response.

        All three scopes answer with the same envelope: a ``statistics`` summary
        plus a ``records`` list, one entry per sub-period. Which field identifies
        a record depends on the scope (``year`` for /total, ``month`` for /year).
        Records arrive in string order, so the caller must not rely on ordering.
        """
        body = await self._request(
            "GET",
            PATH_HISTORY_STATS.format(station_id=station_id, scope=scope),
            params=params,
        )
        if not isinstance(body, dict):
            raise SolarmanApiError(f"Unexpected history response: {body}")
        totals: dict[int, float] = {}
        for record in body.get("records") or []:
            value = record.get("generationValue")
            key = record.get(field)
            if value is None or key is None:
                continue
            try:
                totals[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return totals

    async def async_get_yearly_production(self, station_id: int) -> dict[int, float]:
        """Return ``{year: kWh}`` for every year the station has data for."""
        return await self._async_history_totals(station_id, "total", "year")

    async def async_get_monthly_production(
        self, station_id: int, year: int
    ) -> dict[int, float]:
        """Return ``{month: kWh}`` for one year; months without data are absent."""
        # aiohttp rejects non-string query values, same as form fields.
        return await self._async_history_totals(
            station_id, "year", "month", {"year": str(year)}
        )

    async def async_get_daily_production(
        self, station_id: int, year: int, month: int
    ) -> dict[int, float]:
        """Return ``{day: kWh}`` for one month; days without data are absent."""
        return await self._async_history_totals(
            station_id, "month", "day", {"year": str(year), "month": str(month)}
        )

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


# Station history granularities of the official API. The period bounds are
# inclusive and must be written as ``yyyy-MM-dd`` for days and ``yyyy-MM`` for
# months; a day-style bound on the month scale is refused with "invalid param".
_HISTORY_DAYS = 2
_HISTORY_MONTHS = 3

# The official API answers errors with HTTP 200 and ``success: false``. An
# expired token shows up that way too, so a message mentioning the token is
# answered by signing in again before giving up.
_TOKEN_HINTS = ("token", "auth")

# How far back the first history scan looks for a year with data.
_MAX_HISTORY_YEARS = 15


def _as_float(value: Any) -> float | None:
    """Read a number the API may send as a string, or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SolarmanOpenApi:
    """Async client for Solarman's official OpenAPI (App ID + App Secret).

    Signing in takes the account e-mail and the SHA-256 digest of its password;
    the plain password is never needed, so the caller stores only the digest.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        app_id: str,
        app_secret: str,
        email: str,
        password_hash: str,
        base_url: str,
    ) -> None:
        """Initialise the client with the developer and account credentials."""
        self._session = session
        self._app_id = app_id
        self._app_secret = app_secret
        self._email = email
        self._password_hash = password_hash
        self._base_url = base_url.rstrip("/")

        self._access_token: str | None = None
        self._expires_at: float = 0.0

    # -- auth ---------------------------------------------------------------

    async def async_login(self) -> None:
        """Obtain an access token (valid for about 60 days)."""
        body = await self._post(
            OPENAPI_PATH_TOKEN,
            {
                "appSecret": self._app_secret,
                "email": self._email,
                "password": self._password_hash,
            },
            params={"appId": self._app_id, "language": "en"},
            authed=False,
        )
        if body is _UNAUTHORIZED:
            raise SolarmanAuthError("Solarman OpenAPI sign-in rejected (HTTP 401)")
        token = body.get("access_token")
        if body.get("success") is False or not token:
            # Never echo the request: it carries the secret and password digest.
            raise SolarmanAuthError(
                f"Solarman OpenAPI sign-in rejected: "
                f"{body.get('code')} {body.get('msg')}"
            )
        self._access_token = str(token)
        expires_in = int(_as_float(body.get("expires_in")) or 86400)
        self._expires_at = time.time() + max(expires_in - 3600, 60)

    async def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an authenticated request, signing in again once if refused."""
        if self._access_token is None or time.time() >= self._expires_at:
            await self.async_login()
        body = await self._post(path, payload)
        if body is _UNAUTHORIZED or _looks_like_token_error(body):
            await self.async_login()
            body = await self._post(path, payload)
            if body is _UNAUTHORIZED:
                raise SolarmanAuthError("Still unauthorized after signing in again")
        if body.get("success") is False:
            raise SolarmanApiError(
                f"{path} failed: {body.get('code')} {body.get('msg')}"
            )
        return body

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        params: dict[str, str] | None = None,
        authed: bool = True,
    ) -> Any:
        """Make one POST and return the decoded body, or ``_UNAUTHORIZED``."""
        headers = {"Content-Type": "application/json"}
        if authed:
            headers["Authorization"] = f"bearer {self._access_token}"
        try:
            async with self._session.post(
                self._base_url + path,
                json=payload,
                params=params or {"language": "en"},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    return _UNAUTHORIZED
                status = resp.status
                raw = await resp.text()
        except aiohttp.ClientError as err:
            raise SolarmanApiError(f"Request to {path} failed: {err}") from err
        try:
            body = json_loads(raw)
        except ValueError:
            body = None
        if status >= 400 or not isinstance(body, dict):
            raise SolarmanApiError(f"{path} answered HTTP {status}: {raw[:300]}")
        return body

    # -- high level ---------------------------------------------------------

    async def async_get_stations(self) -> list[dict[str, Any]]:
        """Return the account's stations."""
        body = await self._request(
            OPENAPI_PATH_STATION_LIST, {"page": 1, "size": 100}
        )
        return body.get("stationList") or []

    async def async_get_station_detail(self, station_id: int) -> dict[str, Any]:
        """Return the station's list entry, which carries its static detail."""
        for station in await self.async_get_stations():
            if station.get("id") == station_id:
                return station
        raise SolarmanApiError(f"Station {station_id} not found")

    async def async_get_station(self, station_id: int) -> dict[str, Any]:
        """Return the live summary for one station, under the portal's names.

        The list entry supplies the network status and last update time, the
        real-time call the current power. Energy totals the real-time answer
        lacks are filled in by the coordinator from the production history.
        """
        data = dict(await self.async_get_station_detail(station_id))
        realtime = await self._request(
            OPENAPI_PATH_STATION_REALTIME, {"stationId": station_id}
        )
        for key, value in realtime.items():
            if key not in ("code", "msg", "success", "requestId") and value is not None:
                data[key] = value
        # Same meaning, different name from the portal's.
        if data.get("buyPower") is None and data.get("purchasePower") is not None:
            data["buyPower"] = data["purchasePower"]
        return data

    async def _async_history(
        self, station_id: int, time_type: int, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Return the station history records between two inclusive bounds."""
        body = await self._request(
            OPENAPI_PATH_STATION_HISTORY,
            {
                "stationId": station_id,
                "timeType": time_type,
                "startTime": start,
                "endTime": end,
            },
        )
        return body.get("stationDataItems") or []

    async def async_get_monthly_production(
        self, station_id: int, year: int
    ) -> dict[int, float]:
        """Return ``{month: kWh}`` for one year; months without data are absent."""
        today = dt_util.now().date()
        if year > today.year:
            return {}
        last_month = today.month if year == today.year else 12
        totals: dict[int, float] = {}
        for record in await self._async_history(
            station_id, _HISTORY_MONTHS, f"{year}-01", f"{year}-{last_month:02d}"
        ):
            value = _as_float(record.get("generationValue"))
            month = record.get("month")
            if value is None or not month:
                continue
            totals[int(month)] = value
        return totals

    async def async_get_yearly_production(self, station_id: int) -> dict[int, float]:
        """Return ``{year: kWh}`` for every year the station has data for.

        Built from the monthly scale, whose request format is known to work,
        walking back from this year until a year comes back empty.
        """
        years: dict[int, float] = {}
        year = dt_util.now().year
        for _ in range(_MAX_HISTORY_YEARS):
            months = await self.async_get_monthly_production(station_id, year)
            if not months:
                break
            years[year] = sum(months.values())
            year -= 1
        return years

    async def async_get_daily_production(
        self, station_id: int, year: int, month: int
    ) -> dict[int, float]:
        """Return ``{day: kWh}`` for one month; days without data are absent."""
        today = dt_util.now().date()
        first = date(year, month, 1)
        if first > today:
            return {}
        last = min(date(year, month, calendar.monthrange(year, month)[1]), today)
        totals: dict[int, float] = {}
        for record in await self._async_history(
            station_id, _HISTORY_DAYS, first.isoformat(), last.isoformat()
        ):
            value = _as_float(record.get("generationValue"))
            day = record.get("day")
            if value is None or not day:
                continue
            totals[int(day)] = value
        return totals

    async def async_get_devices(self, station_id: int) -> list[dict[str, Any]]:
        """Return the devices (inverter, logger, ...) for a station."""
        body = await self._request(
            OPENAPI_PATH_STATION_DEVICES, {"stationId": station_id}
        )
        return body.get("deviceListItems") or []


def _looks_like_token_error(body: Any) -> bool:
    """Whether a ``success: false`` answer blames the access token."""
    if not isinstance(body, dict) or body.get("success") is not False:
        return False
    message = f"{body.get('code')} {body.get('msg')}".lower()
    return any(hint in message for hint in _TOKEN_HINTS)
