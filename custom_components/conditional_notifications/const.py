"""Constants for Conditional Notifications."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "conditional_notifications"
NAME: Final = "Conditional Notifications"
VERSION: Final = "1.2.5"
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
PLATFORMS: Final = ["sensor"]
PANEL_URL: Final = "/conditional_notifications_panel.js"
PANEL_PATH: Final = "conditional-notifications"
SIGNAL_CHANGED: Final = f"{DOMAIN}_changed"

DEFAULT_OPTIONS: Final = {
    "panel_enabled": True,
    "history_retention_days": 30,
    "history_max_records": 500,
    "retain_content": True,
    "delivery": {
        "persistent_notification": True,
        "notify_entities": [],
        "notify_services": [],
        "assist_satellites": [],
    },
}

EVENTS: Final = {
    "created",
    "updated",
    "triggered",
    "resolved",
    "expired",
    "paused",
    "resumed",
    "rearmed",
    "deleted",
}

UNKNOWN_STATES: Final = {"unknown", "unavailable"}
WEEKDAYS: Final = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
