"""The Solarman Cloud integration."""
from __future__ import annotations

from homeassistant.components import webhook as webhook_component
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import webhook as token_webhook
from .const import (
    AUTH_MODE_PORTAL,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_WEBHOOK_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import SolarmanCoordinator, credentials

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solarman Cloud from a config entry."""
    if not entry.data.get(CONF_WEBHOOK_ID):
        # Entries created before the webhook existed get one on first load.
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_WEBHOOK_ID: webhook_component.async_generate_id(),
            },
        )

    coordinator = SolarmanCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    if coordinator.auth_mode == AUTH_MODE_PORTAL:
        # Only the portal token needs feeding from the browser.
        await token_webhook.async_register(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    token_webhook.async_unregister(hass, entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the interval or the way of signing in actually changed.

    Every token rotation rewrites entry.data, which also fires this listener.
    Reloading on those would restart the integration constantly, so a portal
    token only counts as changed when the running client does not already hold
    it - which is the case after reauth, but not after a rotation or a token
    delivered by the browser script. Reconfigure and reauth rely on this
    listener for their reload.
    """
    coordinator: SolarmanCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    new_interval = int(
        entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
    )
    token_changed = (
        coordinator.auth_mode == AUTH_MODE_PORTAL
        and entry.data.get(CONF_REFRESH_TOKEN) != coordinator.api.refresh_token
    )
    if (
        coordinator.scan_interval != new_interval
        or coordinator.credentials != credentials(entry.data)
        or token_changed
    ):
        await hass.config_entries.async_reload(entry.entry_id)
