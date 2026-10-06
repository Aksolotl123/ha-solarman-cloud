"""The token webhook URL must never leak into logs, entity states or attributes.

Its random id is the endpoint's only protection (it is registered with
``local_only=False``), so anything every HA user can read - the log, an entity's
state and attributes, and through them the recorder and backups - must not
contain it. Only the admin-only options flow may show it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.components.diagnostics import REDACTED, async_redact_data

from custom_components.solarman_cloud import webhook as token_webhook
from custom_components.solarman_cloud.config_flow import SolarmanOptionsFlow
from custom_components.solarman_cloud.const import (
    AUTH_MODE_OPENAPI,
    AUTH_MODE_PORTAL,
    CONF_AUTH_MODE,
    CONF_WEBHOOK_ID,
)
from custom_components.solarman_cloud.diagnostics import TO_REDACT
from custom_components.solarman_cloud.sensor import SolarmanWebhookSensor

WEBHOOK_ID = "0123456789abcdef" * 4
BASE_URL = "https://ha.example.invalid"


def _entry(mode: str = AUTH_MODE_PORTAL) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.unique_id = "solarman_cloud_1"
    entry.title = "Station"
    entry.data = {CONF_AUTH_MODE: mode, CONF_WEBHOOK_ID: WEBHOOK_ID}
    entry.options = {}
    return entry


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test starts with no registered webhook and no received token."""
    token_webhook._REGISTERED.clear()
    token_webhook._LAST_RECEIVED.clear()
    yield
    token_webhook._REGISTERED.clear()
    token_webhook._LAST_RECEIVED.clear()


async def _register(entry: MagicMock) -> MagicMock:
    hass = MagicMock()
    with patch.object(token_webhook.webhook, "async_register") as register:
        await token_webhook.async_register(hass, entry)
    register.assert_called_once()
    return hass


def _sensor(entry: MagicMock) -> SolarmanWebhookSensor:
    coordinator = MagicMock()
    coordinator.station_id = 1
    return SolarmanWebhookSensor(coordinator, entry)


@pytest.mark.parametrize("received", [False, True])
async def test_entity_state_and_attributes_hold_no_webhook_id(received: bool) -> None:
    entry = _entry()
    await _register(entry)
    if received:
        token_webhook._LAST_RECEIVED[entry.entry_id] = datetime(
            2026, 10, 5, 12, 0, tzinfo=timezone.utc
        )
    sensor = _sensor(entry)

    with patch.object(token_webhook, "get_url", return_value=BASE_URL):
        state = sensor.native_value
        attributes = sensor.extra_state_attributes

    assert state == "active"
    assert WEBHOOK_ID not in str(state)
    assert WEBHOOK_ID not in repr(attributes)
    assert "/api/webhook" not in repr(attributes)
    assert "url" not in attributes
    assert (attributes["last_token_received"] is not None) is received


async def test_entity_inactive_after_unregister() -> None:
    entry = _entry()
    hass = await _register(entry)
    with patch.object(token_webhook.webhook, "async_unregister") as unregister:
        token_webhook.async_unregister(hass, entry)
    unregister.assert_called_once_with(hass, WEBHOOK_ID)
    assert _sensor(entry).native_value == "inactive"


async def test_unregister_uses_registered_id_after_regeneration() -> None:
    """A regenerated id in entry.data must not leave the old webhook live."""
    entry = _entry()
    hass = await _register(entry)
    entry.data = {**entry.data, CONF_WEBHOOK_ID: "f" * 64}
    with patch.object(token_webhook.webhook, "async_unregister") as unregister:
        token_webhook.async_unregister(hass, entry)
    unregister.assert_called_once_with(hass, WEBHOOK_ID)


async def test_register_does_not_log_webhook_id(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with patch.object(token_webhook, "get_url", return_value=BASE_URL):
        await _register(_entry())
    assert "webhook registered" in caplog.text
    assert WEBHOOK_ID not in caplog.text
    assert "/api/webhook" not in caplog.text


def test_build_url_contains_id() -> None:
    """The admin-only channel still gets the full URL."""
    with patch.object(token_webhook, "get_url", return_value=BASE_URL):
        url = token_webhook.build_url(MagicMock(), _entry())
    assert url == f"{BASE_URL}/api/webhook/{WEBHOOK_ID}"


def _options_flow(entry: MagicMock) -> SolarmanOptionsFlow:
    flow = SolarmanOptionsFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    flow.handler = entry.entry_id
    flow.flow_id = "flow1"
    return flow


async def test_options_flow_shows_url_in_token_mode() -> None:
    entry = _entry()
    flow = _options_flow(entry)
    with patch.object(token_webhook, "get_url", return_value=BASE_URL):
        result = await flow.async_step_init()
    assert result["step_id"] == "portal"
    assert result["description_placeholders"]["webhook_url"] == (
        f"{BASE_URL}/api/webhook/{WEBHOOK_ID}"
    )


async def test_options_flow_regenerates_webhook_id() -> None:
    entry = _entry()
    flow = _options_flow(entry)
    result = await flow.async_step_init(
        {"scan_interval": 3600, "regenerate_webhook": True}
    )
    assert result["data"] == {"scan_interval": 3600}
    update = flow.hass.config_entries.async_update_entry
    update.assert_called_once()
    new_id = update.call_args.kwargs["data"][CONF_WEBHOOK_ID]
    assert new_id and new_id != WEBHOOK_ID
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with("entry1")


async def test_options_flow_openapi_has_no_url() -> None:
    flow = _options_flow(_entry(AUTH_MODE_OPENAPI))
    result = await flow.async_step_init()
    assert result["step_id"] == "init"
    assert not result.get("description_placeholders")


def test_diagnostics_redacts_station_identity() -> None:
    data = {
        "station_name": "Dom Kowalskich",
        "name": "Dom Kowalskich",
        "stationImage": "https://img.example.invalid/x.jpg",
        CONF_WEBHOOK_ID: WEBHOOK_ID,
        "station_id": 4242,
        "id": 4242,
        "stationId": 4242,
        "regionTimezone": "Europe/Warsaw",
        "generationPower": 1234,
    }
    redacted = async_redact_data(data, TO_REDACT)
    for key in (
        "station_name", "name", "stationImage", CONF_WEBHOOK_ID,
        "station_id", "id", "stationId", "regionTimezone",
    ):
        assert redacted[key] == REDACTED
    assert redacted["generationPower"] == 1234
