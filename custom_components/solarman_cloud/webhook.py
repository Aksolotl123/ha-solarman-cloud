"""Webhook that receives a refresh token captured in the user's browser.

Home Assistant cannot read a cookie belonging to another site, so a small script
running on the Solarman page posts the token here after the user has signed in
(and solved the slider CAPTCHA) themselves. Nothing about the CAPTCHA is bypassed:
a human completes the login, and only the resulting token is forwarded.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from json import JSONDecodeError, loads

from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .api import SolarmanApiError, SolarmanAuthError, SolarmanCloudApi, token_expiry
from .const import (
    CONF_BASE_URL,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_WEBHOOK_ID,
    DEFAULT_BASE_URL,
    DEFAULT_REGION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Tokens are JWTs; anything much shorter is not worth sending to the server.
MIN_TOKEN_LENGTH = 100
MAX_TOKEN_LENGTH = 8000

# The browser script can fire several times at once (page load plus its periodic
# check). Each accepted post spends a token, so identical posts are handled once.
_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_ACCEPTED: dict[str, str] = {}

# The webhook id actually registered for each entry. Unregistering goes by this
# value, not by entry.data, so a regenerated id never leaves the old one live.
_REGISTERED: dict[str, str] = {}
# When a token posted by the browser was last accepted, per entry.
_LAST_RECEIVED: dict[str, datetime] = {}


def is_registered(entry: ConfigEntry) -> bool:
    """Whether this entry's webhook is currently listening."""
    return entry.entry_id in _REGISTERED


def last_received(entry: ConfigEntry) -> datetime | None:
    """When the browser last delivered a token that was accepted."""
    return _LAST_RECEIVED.get(entry.entry_id)


def build_url(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """The full webhook URL for the userscript.

    The webhook id is the only thing protecting the endpoint, so this URL must
    never reach a log, an entity state or an attribute. It is shown only in the
    options flow, which only administrators can open.
    """
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        return None
    try:
        # Prefer an internet-reachable URL: the browser may be off-network.
        base = get_url(hass, allow_cloud=True, prefer_external=True)
    except NoURLAvailableError:
        base = ""
    return f"{base}{webhook.async_generate_path(webhook_id)}"


async def async_register(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register this entry's token-receiving webhook."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        return

    async def _handle(
        hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response:
        return await _async_handle_token(hass, entry, request)

    webhook.async_register(
        hass,
        DOMAIN,
        f"Solarman Cloud token ({entry.title})",
        webhook_id,
        _handle,
        allowed_methods=["POST"],
        local_only=False,
    )
    _REGISTERED[entry.entry_id] = webhook_id
    # The URL itself is a secret and is deliberately not logged.
    _LOGGER.info(
        "Solarman token webhook registered; its URL is shown in the "
        "integration options (Configure)"
    )


async def _async_handle_token(
    hass: HomeAssistant, entry: ConfigEntry, request: Request
) -> Response:
    """Validate a posted token and store it on the config entry."""
    try:
        token = (await request.text()).strip()
    except Exception:  # noqa: BLE001 - malformed body
        return Response(status=400, text="bad request")

    # The script may post the raw token or a tiny JSON envelope.
    if token.startswith("{"):
        try:
            token = str(loads(token).get("refresh_token", "")).strip()
        except (JSONDecodeError, AttributeError):
            return Response(status=400, text="bad json")

    if not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH:
        _LOGGER.warning("Solarman webhook got a token of implausible length")
        return Response(status=400, text="implausible token")

    lock = _LOCKS.setdefault(entry.entry_id, asyncio.Lock())
    async with lock:
        if token in (entry.data.get(CONF_REFRESH_TOKEN), _LAST_ACCEPTED.get(entry.entry_id)):
            # Already current, or a duplicate of the post just handled. Comparing
            # against the posted value matters because accepting one rotates it,
            # so the stored token no longer matches what the browser keeps sending.
            return Response(status=200, text="unchanged")

        api = SolarmanCloudApi(
            async_get_clientsession(hass),
            token,
            entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            entry.data.get(CONF_REGION, DEFAULT_REGION),
        )
        try:
            await api.async_refresh()
        except (SolarmanAuthError, SolarmanApiError) as err:
            _LOGGER.warning("Solarman webhook rejected a token: %s", err)
            return Response(status=400, text="token rejected")

        # async_refresh may already have rotated it; store the newest one.
        newest = api.refresh_token
        _LAST_ACCEPTED[entry.entry_id] = token

        # Hand the token to the running client instead of reloading the entry.
        # This goes first: the update listener reloads when the stored token
        # differs from the one the client holds.
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None:
            coordinator.api.set_refresh_token(newest)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: newest}
        )
        _LAST_RECEIVED[entry.entry_id] = dt_util.utcnow()
        if coordinator is not None:
            # Let the token and webhook diagnostics show the new values now.
            coordinator.async_update_listeners()

        exp = token_expiry(newest)
        _LOGGER.info(
            "Solarman refresh token accepted from browser; valid until %s",
            exp.isoformat() if exp else "unknown",
        )
        return Response(status=200, text="ok")


def async_unregister(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the webhook when the entry is unloaded."""
    if webhook_id := _REGISTERED.pop(entry.entry_id, None):
        webhook.async_unregister(hass, webhook_id)
