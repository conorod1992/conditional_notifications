"""Persistent models and pure timing helpers."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.util import dt as dt_util

from .const import WEEKDAYS


def utc_iso(now: datetime | None = None) -> str:
    """Return a timezone-aware ISO timestamp."""
    value = now or datetime.now().astimezone()
    if value.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return value.isoformat()


def parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp defensively and require an offset."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("datetime must be an ISO timestamp string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ValueError("datetime must be a valid ISO timestamp") from err
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset")
    return result


def parse_duration(value: Any) -> timedelta:
    """Parse finite seconds or a HA-style duration mapping."""
    if value in (None, "", 0, 0.0) and not isinstance(value, bool):
        return timedelta(0)
    if isinstance(value, bool):
        raise ValueError("duration must not be a boolean")
    if isinstance(value, int | float):
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError("duration must be finite")
        return timedelta(seconds=seconds)
    if isinstance(value, dict):
        allowed = {"days", "hours", "minutes", "seconds"}
        if set(value) - allowed:
            raise ValueError("duration contains unsupported fields")
        normalized: dict[str, float] = {}
        for key, raw in value.items():
            if isinstance(raw, bool):
                raise ValueError("duration fields must not be booleans")
            number = float(raw)
            if not math.isfinite(number):
                raise ValueError("duration fields must be finite")
            normalized[key] = number
        return timedelta(**normalized)
    raise ValueError("duration must be seconds or a duration object")


def duration_seconds(value: Any) -> float:
    """Return validated non-negative duration seconds."""
    seconds = parse_duration(value).total_seconds()
    if seconds < 0:
        raise ValueError("duration cannot be negative")
    return seconds


def in_recurring_window(now: datetime, window: dict[str, Any] | None) -> bool:
    """Check a local recurring window, including overnight weekday semantics."""
    if not window:
        return True
    start = time.fromisoformat(window["start"])
    end = time.fromisoformat(window["end"])
    weekdays = window.get("weekdays", list(WEEKDAYS))
    current = now.timetz().replace(tzinfo=None)
    day = WEEKDAYS[now.weekday()]
    if start == end:
        return day in weekdays
    if start < end:
        return day in weekdays and start <= current < end
    if current >= start:
        return day in weekdays
    previous_day = WEEKDAYS[(now.weekday() - 1) % 7]
    return current < end and previous_day in weekdays


@dataclass(slots=True)
class HistoryItem:
    """One bounded meaningful lifecycle event."""

    id: str
    notification_id: str
    timestamp: str
    event: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    owner_id: str | None = None

    @classmethod
    def create(
        cls,
        notification_id: str,
        event: str,
        summary: str,
        details: dict[str, Any] | None = None,
        owner_id: str | None = None,
    ) -> HistoryItem:
        return cls(uuid4().hex, notification_id, utc_iso(), event, summary, details or {}, owner_id)


@dataclass(slots=True)
class NotificationRecord:
    """Integration-owned definition plus durable runtime state."""

    id: str
    name: str
    owner_id: str | None
    created_at: str
    updated_at: str
    definition: dict[str, Any]
    semantic_key: str | None = None
    description: str | None = None
    enabled: bool = True
    paused: bool = False
    status: str = "watching"
    revision: int = 1
    notification_count: int = 0
    qualifying_match_seen: bool = False
    last_accepted_at: str | None = None
    last_trigger_at: str | None = None
    last_trigger: dict[str, Any] | None = None
    last_ignored_reason: str | None = None
    last_delivery: list[dict[str, Any]] = field(default_factory=list)
    active_occurrence: bool = False

    @classmethod
    def create(cls, definition: dict[str, Any], owner_id: str | None) -> NotificationRecord:
        now = utc_iso()
        return cls(
            id=uuid4().hex,
            name=definition["name"],
            owner_id=owner_id,
            semantic_key=definition.get("semantic_key"),
            description=definition.get("description"),
            created_at=now,
            updated_at=now,
            definition=definition,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationRecord:
        """Restore one record only when its persisted envelope is structurally safe."""
        if not isinstance(data, dict):
            raise ValueError("record must be an object")

        fields = cls.__dataclass_fields__
        values = {key: value for key, value in data.items() if key in fields}

        for key in ("id", "name", "created_at", "updated_at"):
            value = values.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(f"record {key} must be a non-empty string")

        for key in ("created_at", "updated_at"):
            if parse_datetime(values[key]) is None:
                raise ValueError(f"record {key} is required")

        definition = values.get("definition")
        if not isinstance(definition, dict):
            raise ValueError("record definition must be an object")

        owner_id = values.get("owner_id")
        if owner_id is not None and not isinstance(owner_id, str):
            raise ValueError("record owner_id must be a string or null")
        values.setdefault("owner_id", None)

        for key in ("semantic_key", "description", "last_ignored_reason"):
            value = values.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"record {key} must be a string or null")

        for key in ("last_accepted_at", "last_trigger_at"):
            value = values.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"record {key} must be a timestamp string or null")
                if value and parse_datetime(value) is None:
                    raise ValueError(f"record {key} must be a valid timestamp")

        for key in ("enabled", "paused", "qualifying_match_seen", "active_occurrence"):
            if key in values and not isinstance(values[key], bool):
                raise ValueError(f"record {key} must be true or false")

        for key, minimum in (("revision", 1), ("notification_count", 0)):
            if key in values:
                value = values[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                    raise ValueError(f"record {key} must be an integer >= {minimum}")

        status = values.get("status")
        if status is not None and (not isinstance(status, str) or not status):
            raise ValueError("record status must be a non-empty string")

        last_trigger = values.get("last_trigger")
        if last_trigger is not None and not isinstance(last_trigger, dict):
            raise ValueError("record last_trigger must be an object or null")

        last_delivery = values.get("last_delivery")
        if last_delivery is not None and (
            not isinstance(last_delivery, list)
            or any(not isinstance(item, dict) for item in last_delivery)
        ):
            raise ValueError("record last_delivery must be a list of objects")

        return cls(**values)

    def is_temporally_active(self, now: datetime) -> bool:
        available = parse_datetime(self.definition.get("available_from"))
        expires = parse_datetime(self.definition.get("expires_at"))
        return (
            (not available or now >= available)
            and (not expires or now < expires)
            and in_recurring_window(now, self.definition.get("active_window"))
        )

    def remaining(self) -> int | None:
        if self.definition.get("repeat_policy") != "limited":
            return None
        return max(0, int(self.definition["max_notifications"]) - self.notification_count)

    def public_dict(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or dt_util.now()
        result = self.as_dict()
        result["currently_active"] = self.is_temporally_active(current)
        result["remaining_notifications"] = self.remaining()
        cooldown = duration_seconds(self.definition.get("cooldown"))
        next_eligible = None
        if cooldown and self.last_accepted_at:
            accepted_at = parse_datetime(self.last_accepted_at)
            assert accepted_at is not None
            next_eligible = (accepted_at + timedelta(seconds=cooldown)).isoformat()
        result["next_eligible_at"] = next_eligible
        return result
