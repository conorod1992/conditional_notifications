"""Versioned persistence for definitions and bounded history."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import HistoryItem, NotificationRecord, parse_datetime

_LOGGER = logging.getLogger(__name__)


class _VersionedStore(Store[dict[str, Any]]):
    """Provide an explicit migration seam for future storage evolution."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version == 0:
            return {
                "records": old_data.get("records", old_data.get("watches", [])),
                "invalid_records": old_data.get("invalid_records", []),
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
        self.invalid_records: list[dict[str, Any]] = []
        self.history: list[HistoryItem] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.records = {}
        self.invalid_records = [
            item for item in data.get("invalid_records", []) if isinstance(item, dict)
        ]
        for item in data.get("records", []):
            try:
                if not isinstance(item, dict) or not item.get("id"):
                    raise ValueError("record must be an object with an id")
                record = NotificationRecord.from_dict(item)
            except (TypeError, ValueError) as err:
                if isinstance(item, dict):
                    self.invalid_records.append(item)
                _LOGGER.warning("Ignoring malformed Conditional Notifications record: %s", err)
                continue
            if record.id in self.records:
                self.invalid_records.append(item)
                _LOGGER.warning(
                    "Ignoring duplicate Conditional Notifications record id %s", record.id
                )
                continue
            self.records[record.id] = record

        self.history = []
        for item in data.get("history", []):
            try:
                if not isinstance(item, dict):
                    raise ValueError("history item must be an object")
                self.history.append(HistoryItem(**item))
            except (TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Ignoring malformed Conditional Notifications history item: %s", err
                )

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "records": [record.as_dict() for record in self.records.values()],
                "invalid_records": list(self.invalid_records),
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
