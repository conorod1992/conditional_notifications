"""Versioned persistence for definitions and bounded history."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import HistoryItem, NotificationRecord, parse_datetime


class _VersionedStore(Store[dict[str, Any]]):
    """Provide an explicit migration seam for future storage evolution."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version == 0:
            return {
                "records": old_data.get("records", old_data.get("watches", [])),
                "history": old_data.get("history", []),
            }
        raise NotImplementedError


class NotificationStore:
    """Own the single atomic versioned storage document."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = _VersionedStore(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        self.records: dict[str, NotificationRecord] = {}
        self.history: list[HistoryItem] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.records = {
            item["id"]: NotificationRecord.from_dict(item) for item in data.get("records", [])
        }
        self.history = [HistoryItem(**item) for item in data.get("history", [])]

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "records": [record.as_dict() for record in self.records.values()],
                "history": [
                    {
                        "id": item.id,
                        "notification_id": item.notification_id,
                        "timestamp": item.timestamp,
                        "event": item.event,
                        "summary": item.summary,
                        "details": item.details,
                        "owner_id": item.owner_id,
                    }
                    for item in self.history
                ],
            }
        )

    def add_history(self, item: HistoryItem, *, days: int, maximum: int) -> None:
        self.history.append(item)
        cutoff = datetime.now().astimezone() - timedelta(days=days)
        retained: list[HistoryItem] = []
        for entry in self.history:
            timestamp = parse_datetime(entry.timestamp)
            if timestamp is not None and timestamp >= cutoff:
                retained.append(entry)
        self.history = retained[-maximum:]

    def history_for(self, notification_id: str | None = None) -> list[dict[str, Any]]:
        items = self.history
        if notification_id:
            items = [item for item in items if item.notification_id == notification_id]
        return [
            {
                "id": item.id,
                "notification_id": item.notification_id,
                "timestamp": item.timestamp,
                "event": item.event,
                "summary": item.summary,
                "details": item.details,
                "owner_id": item.owner_id,
            }
            for item in reversed(items)
        ]
