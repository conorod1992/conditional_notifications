"""One compact bounded summary sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_CHANGED
from .manager import NotificationManager


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ConditionalNotificationsSensor(entry.runtime_data)])


class ConditionalNotificationsSensor(SensorEntity):
    """Small aggregate only; definitions remain in storage/WebSocket API."""

    _attr_has_entity_name = True
    _attr_name = "Active"
    _attr_icon = "mdi:bell-badge-outline"
    _attr_unique_id = "conditional_notifications_summary"

    def __init__(self, manager: NotificationManager) -> None:
        self.manager = manager

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_CHANGED, self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return sum(
            1
            for item in self.manager.store.records.values()
            if item.enabled and not item.paused and item.status != "expired"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        records = list(self.manager.store.records.values())
        last = max(
            (r for r in records if r.last_trigger_at),
            key=lambda r: r.last_trigger_at or "",
            default=None,
        )
        return {
            "active_count": self.native_value,
            "paused_count": sum(1 for r in records if r.paused),
            "expired_count": sum(1 for r in records if r.status == "expired"),
            "last_triggered_at": last.last_trigger_at if last else None,
        }
