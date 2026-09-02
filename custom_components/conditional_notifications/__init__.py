"""Conditional Notifications integration setup."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_PATH, PANEL_URL, PLATFORMS, VERSION
from .llm import async_register_llm_api
from .optimized_manager import LifecycleNotificationManager
from .services import async_register_services, async_unregister_services
from .websocket import async_register_websocket

type ConditionalNotificationsConfigEntry = ConfigEntry[LifecycleNotificationManager]

_PANEL_ASSET_REVISION = "translations1"
_PANEL_MODULE_FILES = (
    "conditional-notifications-panel.js",
    "conditional-notifications-panel-status.js",
    "conditional-notifications-panel-correlation.js",
    "conditional-notifications-panel-lifecycle.js",
    "conditional-notifications-panel-entry.js",
    "conditional-notifications-panel-native-automation.js",
    "conditional-notifications-panel-editor-ux.js",
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConditionalNotificationsConfigEntry
) -> bool:
    """Set up the sole config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault("subscribers", set())

    manager = LifecycleNotificationManager(hass, dict(entry.options))
    await manager.async_initialize()
    entry.runtime_data = manager
    domain_data["manager"] = manager

    async_register_services(hass, manager)
    if not domain_data.get("websocket_registered"):
        async_register_websocket(hass)
        domain_data["websocket_registered"] = True
    entry.async_on_unload(async_register_llm_api(hass, manager))

    if manager.options.get("panel_enabled", True):
        panel_dir = Path(__file__).parent / "frontend"
        panel_file = panel_dir / "conditional-notifications-panel-performance.js"
        if not domain_data.get("static_paths_registered"):
            static_paths = [
                StaticPathConfig(PANEL_URL, str(panel_file), cache_headers=False),
                *(
                    StaticPathConfig(
                        f"/{filename}",
                        str(panel_dir / filename),
                        cache_headers=False,
                    )
                    for filename in _PANEL_MODULE_FILES
                ),
            ]
            await hass.http.async_register_static_paths(static_paths)
            domain_data["static_paths_registered"] = True
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
    manager.broadcast_reload()
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
    domain_data = hass.data.get(DOMAIN)
    if domain_data is not None and domain_data.get("manager") is entry.runtime_data:
        domain_data.pop("manager", None)
    return True
