"""Persistent models and pure timing helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from uuid import uuid4

from .const import WEEKDAYS


def utc_iso(now: datetime | None = None) -> str:
    """Return a timezone-aware ISO timestamp."""
    value = now or datetime.now().astimezone()
    if value.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return value.isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO timestamp and require an offset."""
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("datetime must include a timezone offset")
    return result


def parse_duration(value: Any) -> timedelta:
    """Parse seconds or a HA-style duration mapping."""
    if value in (None, "", 0, 0.0):
        return timedelta(0)
    if isinstance(value, int | float):
        return timedelta(seconds=float(value))
    if isinstance(value, dict):
        allowed = {"days", "hours", "minutes", "seconds"}
        if set(value) - allowed:
            raise ValueError("duration contains unsupported fields")
        return timedelta(**{key: float(val) for key, val in value.items()})
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

    @classmethod
    def create(
        cls, notification_id: str, event: str, summary: str, details: dict[str, Any] | None = None
    ) -> HistoryItem:
        return cls(uuid4().hex, notification_id, utc_iso(), event, summary, details or {})


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
        fields = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in fields})

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
        current = now or datetime.now().astimezone()
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
