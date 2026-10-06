"""The token webhook accepts only a token for this entry's own station.

Whoever learns the webhook URL can post to it. A working refresh token for some
other Solarman account must not replace the stored one, an oversized body must
be refused before it is read whole, and no failure may escape the handler
(Home Assistant logs escaping exceptions together with the webhook id). The
failed-refresh message must not echo the token back into the log or the error.
"""
from __future__ import annotations

import base64
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.util.aiohttp import MockRequest

from custom_components.solarman_cloud import webhook as token_webhook
from custom_components.solarman_cloud.api import (
    SolarmanApiError,
    SolarmanAuthError,
    SolarmanCloudApi,
    _error_summary,
    scrub_tokens,
)
from custom_components.solarman_cloud.const import (
    CONF_REFRESH_TOKEN,
    CONF_STATION_ID,
    CONF_WEBHOOK_ID,
)

STATION_ID = 4242


def _jwt(tag: str) -> str:
    """A made-up JWT-shaped token, long enough for the webhook."""

    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'sub': tag, 'pad': 'x' * 80})}.c2lnbmF0dXJlLWZha2U"


OLD_TOKEN = _jwt("stored")
POSTED_TOKEN = _jwt("posted")
ROTATED_TOKEN = _jwt("rotated")


def _entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.title = "Station"
    entry.data = {
        CONF_WEBHOOK_ID: "0123456789abcdef" * 4,
        CONF_STATION_ID: STATION_ID,
        CONF_REFRESH_TOKEN: OLD_TOKEN,
    }
    return entry


def _hass() -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    return hass


class _FakeApi:
    """Stands in for SolarmanCloudApi inside the webhook."""

    refresh_error: BaseException | None = None
    stations_error: BaseException | None = None
    stations: list = []

    def __init__(self, session, token, base_url, region) -> None:
        self.refresh_token = token

    async def async_refresh(self) -> None:
        if self.refresh_error is not None:
            raise self.refresh_error
        self.refresh_token = ROTATED_TOKEN

    async def async_get_stations(self) -> list:
        if self.stations_error is not None:
            raise self.stations_error
        return self.stations


@pytest.fixture(autouse=True)
def _clean_state():
    token_webhook._LAST_ACCEPTED.clear()
    token_webhook._LAST_RECEIVED.clear()
    _FakeApi.refresh_error = None
    _FakeApi.stations_error = None
    _FakeApi.stations = [{"id": STATION_ID}]
    yield
    token_webhook._LAST_ACCEPTED.clear()
    token_webhook._LAST_RECEIVED.clear()


async def _post(body: str | bytes, request=None):
    hass, entry = _hass(), _entry()
    if request is None:
        content = body.encode() if isinstance(body, str) else body
        # MockRequest is what Home Assistant Cloud hands to webhooks; it has no
        # content_length, so the bounded read is the path exercised.
        request = MockRequest(content, mock_source="test", method="POST")
    with (
        patch.object(token_webhook, "SolarmanCloudApi", _FakeApi),
        patch.object(token_webhook, "async_get_clientsession"),
    ):
        response = await token_webhook._async_handle_token(hass, entry, request)
    return response, hass, entry


def _stored(hass: MagicMock) -> bool:
    return hass.config_entries.async_update_entry.called


async def test_token_for_own_station_is_stored() -> None:
    response, hass, entry = await _post(POSTED_TOKEN)
    assert response.status == 200
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry, data={**entry.data, CONF_REFRESH_TOKEN: ROTATED_TOKEN}
    )


async def test_json_envelope_is_accepted() -> None:
    response, hass, _ = await _post(json.dumps({"refresh_token": POSTED_TOKEN}))
    assert response.status == 200
    assert _stored(hass)


async def test_token_for_another_account_is_refused() -> None:
    _FakeApi.stations = [{"id": STATION_ID + 1}]
    response, hass, _ = await _post(POSTED_TOKEN)
    assert response.status == 403
    assert not _stored(hass)
    assert not token_webhook._LAST_ACCEPTED


async def test_account_without_stations_is_refused() -> None:
    _FakeApi.stations = []
    response, hass, _ = await _post(POSTED_TOKEN)
    assert response.status == 403
    assert not _stored(hass)


async def test_malformed_station_list_does_not_match() -> None:
    _FakeApi.stations = [{"id": str(STATION_ID)}, "junk", None]
    response, hass, _ = await _post(POSTED_TOKEN)
    assert response.status == 403
    assert not _stored(hass)


async def test_rejected_token_is_400() -> None:
    _FakeApi.refresh_error = SolarmanAuthError("Refresh token rejected")
    response, hass, _ = await _post(POSTED_TOKEN)
    assert response.status == 400
    assert not _stored(hass)


async def test_station_list_failure_stores_nothing() -> None:
    _FakeApi.stations_error = SolarmanApiError("Request to /station failed")
    response, hass, _ = await _post(POSTED_TOKEN)
    assert response.status == 503
    assert not _stored(hass)


@pytest.mark.parametrize("where", ["refresh", "stations"])
async def test_unexpected_errors_do_not_escape(
    where: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    if where == "refresh":
        _FakeApi.refresh_error = TimeoutError()
    else:
        _FakeApi.stations_error = ValueError("not json")
    response, hass, entry = await _post(POSTED_TOKEN)
    assert response.status == 503
    assert not _stored(hass)
    assert entry.data[CONF_WEBHOOK_ID] not in caplog.text
    assert POSTED_TOKEN not in caplog.text


async def test_declared_oversized_body_is_refused_unread() -> None:
    request = MagicMock()
    request.content_length = token_webhook.MAX_BODY_BYTES + 1
    request.content.read = AsyncMock(side_effect=AssertionError("body was read"))
    response, hass, _ = await _post(b"", request=request)
    assert response.status == 413
    assert not _stored(hass)


async def test_undeclared_oversized_body_is_refused() -> None:
    response, hass, _ = await _post(b"x" * (token_webhook.MAX_BODY_BYTES * 4))
    assert response.status == 413
    assert not _stored(hass)


async def test_body_arriving_in_small_chunks_is_read_whole() -> None:
    data = POSTED_TOKEN.encode()
    chunks = [data[i : i + 7] for i in range(0, len(data), 7)] + [b""]
    request = MagicMock()
    request.content_length = None
    request.content.read = AsyncMock(side_effect=chunks)
    response, hass, _ = await _post(b"", request=request)
    assert response.status == 200
    assert _stored(hass)


async def test_body_at_the_limit_is_read() -> None:
    body = json.dumps({"refresh_token": POSTED_TOKEN})
    body += " " * (token_webhook.MAX_BODY_BYTES - len(body))
    response, _, _ = await _post(body)
    assert response.status == 200


def test_scrub_tokens_hides_jwts_and_given_values() -> None:
    text = f"Invalid refresh token: {POSTED_TOKEN} (opaque: s3cr3t-value)"
    out = scrub_tokens(text, "s3cr3t-value", None, "")
    assert POSTED_TOKEN not in out
    assert "s3cr3t-value" not in out
    assert out.startswith("Invalid refresh token: <token>")


def test_error_summary_keeps_only_error_fields() -> None:
    body = {
        "error": "invalid_grant",
        "error_description": f"Invalid refresh token: {POSTED_TOKEN}",
        "refresh_token": POSTED_TOKEN,
        "echo": "anything else",
    }
    out = _error_summary(body, json.dumps(body))
    assert out == "invalid_grant Invalid refresh token: <token>"
    assert _error_summary({"message": "Unauthorized"}, "") == "Unauthorized"
    assert _error_summary({}, "{}") == "no error description"
    assert _error_summary(None, f"<html>{POSTED_TOKEN}</html>") == "<html><token></html>"


async def test_failed_refresh_does_not_echo_the_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    opaque = "opaque-refresh-token-" + "z" * 40
    raw = json.dumps({"code": "AUTH_FAILED", "msg": f"bad token {opaque}", "data": opaque})
    resp = MagicMock(status=401)
    resp.text = AsyncMock(return_value=raw)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = ctx

    api = SolarmanCloudApi(session, opaque, "https://api.example.invalid", "EU")
    with pytest.raises(SolarmanAuthError) as err:
        await api.async_refresh()
    assert opaque not in str(err.value)
    assert opaque not in caplog.text
    assert "AUTH_FAILED" in str(err.value)
    assert "AUTH_FAILED" in caplog.text
