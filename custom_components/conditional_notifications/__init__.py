"""Conditional Notifications integration setup."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_PATH, PANEL_URL, PLATFORMS, VERSION
from .llm import async_register_llm_api
from .manager import NotificationManager
from .services import async_register_services, async_unregister_services
from .websocket import async_register_websocket

type ConditionalNotificationsConfigEntry = ConfigEntry[NotificationManager]

_BASE_PANEL_URL = "/conditional_notifications_panel_base.js"
_PANEL_ASSET_REVISION = "status1"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConditionalNotificationsConfigEntry
) -> bool:
    """Set up the sole config entry."""
    manager = NotificationManager(hass, dict(entry.options))
    await manager.async_initialize()
    entry.runtime_data = manager
    hass.data.setdefault(DOMAIN, {})["manager"] = manager

    async_register_services(hass, manager)
    async_register_websocket(hass)
    entry.async_on_unload(async_register_llm_api(hass, manager))

    if manager.options.get("panel_enabled", True):
        panel_dir = Path(__file__).parent / "frontend"
        panel_file = panel_dir / "conditional-notifications-panel-status.js"
        base_panel_file = panel_dir / "conditional-notifications-panel.js"
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(PANEL_URL, str(panel_file), cache_headers=True),
                StaticPathConfig(_BASE_PANEL_URL, str(base_panel_file), cache_headers=True),
            ]
        )
        frontend.async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Conditional Notifications",
            sidebar_icon="mdi:bell-badge-outline",
            frontend_url_path=PANEL_PATH,
            config={
                "_panel_custom": {
                    "name": "conditional-notifications-panel",
                    "module_url": f"{PANEL_URL}?v={VERSION}-{_PANEL_ASSET_REVISION}",
                    "embed_iframe": False,
                    "trust_external": False,
                    "handle_safe_area": True,
                }
            },
            require_admin=False,
        )
        entry.async_on_unload(lambda: frontend.async_remove_panel(hass, PANEL_PATH))

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: ConditionalNotificationsConfigEntry
) -> bool:
    """Unload every listener and integration-owned registration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    await entry.runtime_data.async_shutdown()
    async_unregister_services(hass)
    hass.data.pop(DOMAIN, None)
    return True
