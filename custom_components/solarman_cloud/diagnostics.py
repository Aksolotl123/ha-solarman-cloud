"""Diagnostics for Solarman Cloud.

The field names and units the cloud answers with are not documented well, so
the raw live summary is the first thing to look at when a sensor looks wrong.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_EMAIL,
    CONF_PASSWORD_HASH,
    CONF_REFRESH_TOKEN,
    CONF_WEBHOOK_ID,
    DOMAIN,
)
from .coordinator import SolarmanCoordinator

TO_REDACT = {
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_EMAIL,
    CONF_PASSWORD_HASH,
    CONF_REFRESH_TOKEN,
    CONF_WEBHOOK_ID,
    # Where the station is.
    "locationLat",
    "locationLng",
    "locationAddress",
    "regionNationId",
    "regionLevel1",
    "regionLevel2",
    "regionLevel3",
    "regionLevel4",
    "regionLevel5",
    "contactPhone",
    "ownerName",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the raw cloud answer next to what the sensors were given."""
    coordinator: SolarmanCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "auth_mode": coordinator.auth_mode,
        "raw_summary": async_redact_data(coordinator.raw, TO_REDACT),
        "summary": async_redact_data(coordinator.data or {}, TO_REDACT),
        "monthly": coordinator.monthly_history(limit=1000),
        "monthly_sum": round(sum(coordinator.monthly.values()), 2),
        "daily": {
            f"{y}-{m:02d}-{d:02d}": v for (y, m, d), v in sorted(coordinator.daily.items())
        },
    }
