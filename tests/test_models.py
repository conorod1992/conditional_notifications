"""Persistent model and timing semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from custom_components.conditional_notifications.models import (
    NotificationRecord,
    duration_seconds,
    in_recurring_window,
    parse_datetime,
)


def test_offset_is_required():
    with pytest.raises(ValueError, match="timezone"):
        parse_datetime("2026-08-15T10:00:00")
    assert parse_datetime("2026-08-15T10:00:00Z").tzinfo is not None


def test_duration_supports_seconds_and_mappings():
    assert duration_seconds(30) == 30
    assert duration_seconds({"hours": 1, "minutes": 2}) == 3720
    with pytest.raises(ValueError):
        duration_seconds(-1)


def test_daytime_recurring_window():
    window = {"start": "09:00", "end": "17:00", "weekdays": ["monday"]}
    assert in_recurring_window(datetime(2026, 8, 17, 12, tzinfo=UTC), window)
    assert not in_recurring_window(datetime(2026, 8, 17, 18, tzinfo=UTC), window)


def test_overnight_window_uses_start_day_for_after_midnight():
    window = {"start": "22:00", "end": "07:00", "weekdays": ["monday"]}
    assert in_recurring_window(datetime(2026, 8, 17, 23, tzinfo=UTC), window)
    assert in_recurring_window(datetime(2026, 8, 18, 6, tzinfo=UTC), window)
    assert not in_recurring_window(datetime(2026, 8, 18, 23, tzinfo=UTC), window)


def test_dst_uses_aware_local_wall_time():
    dublin = ZoneInfo("Europe/Dublin")
    window = {"start": "01:00", "end": "04:00", "weekdays": ["sunday"]}
    assert in_recurring_window(datetime(2026, 3, 29, 3, 30, tzinfo=dublin), window)
    assert not in_recurring_window(datetime(2026, 3, 29, 4, 0, tzinfo=dublin), window)


def test_record_roundtrip_and_limited_remaining():
    definition = {
        "name": "Driveway",
        "triggers": [],
        "repeat_policy": "limited",
        "max_notifications": 3,
    }
    record = NotificationRecord.create(definition, "user-1")
    record.notification_count = 2
    restored = NotificationRecord.from_dict(record.as_dict())
    assert restored.id == record.id
    assert restored.owner_id == "user-1"
    assert restored.remaining() == 1


def test_temporal_intersection():
    record = NotificationRecord.create(
        {
            "name": "Window",
            "triggers": [],
            "repeat_policy": "once",
            "available_from": "2026-08-17T09:00:00+00:00",
            "expires_at": "2026-08-17T17:00:00+00:00",
            "active_window": {"start": "10:00", "end": "16:00", "weekdays": ["monday"]},
        },
        None,
    )
    assert record.is_temporally_active(datetime(2026, 8, 17, 12, tzinfo=UTC))
    assert not record.is_temporally_active(datetime(2026, 8, 17, 9, 30, tzinfo=UTC))
