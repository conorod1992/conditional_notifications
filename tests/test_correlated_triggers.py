"""Tests for bounded all-within trigger correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.lifecycle import (
    LifecycleNotificationManager,
)
from custom_components.conditional_notifications.manager import NotificationManager
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)


def definition(**changes):
    value = {
        "name": "Departure signal",
        "triggers": [
            {"type": "state", "entity_id": "binary_sensor.front_door", "to": "on"},
            {"type": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"},
        ],
        "match": "all_within",
        "match_window": 30,
        "title": "Departure detected",
        "message": "Both signals matched.",
        "repeat_policy": "every",
    }
    value.update(changes)
    return value


def manager() -> LifecycleNotificationManager:
    return object.__new__(LifecycleNotificationManager)


def record() -> NotificationRecord:
    normalized = validate_definition(definition())
    normalized.pop("enabled", None)
    return NotificationRecord.create(normalized, "u1")


def trigger(index: int, name: str) -> dict:
    return {
        "trigger_index": index,
        "type": "state",
        "entity_id": name,
        "friendly_name": name,
    }


def test_validation_accepts_bounded_all_within():
    normalized = validate_definition(definition(match_window={"minutes": 2}))
    assert normalized["match"] == "all_within"
    assert normalized["match_window"] == 120


@pytest.mark.parametrize("window", [0, -1, 86401])
def test_validation_rejects_invalid_correlation_windows(window):
    with pytest.raises(DefinitionError):
        validate_definition(definition(match_window=window))


def test_all_within_requires_multiple_triggers():
    with pytest.raises(DefinitionError, match="at least two"):
        validate_definition(
            definition(
                triggers=[
                    {
                        "type": "state",
                        "entity_id": "binary_sensor.door",
                        "to": "on",
                    }
                ]
            )
        )


def test_any_mode_removes_irrelevant_match_window():
    normalized = validate_definition(definition(match="any", match_window=30))
    assert normalized["match"] == "any"
    assert "match_window" not in normalized


def test_correlation_waits_for_every_trigger_then_returns_combined_context():
    instance = manager()
    item = record()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    assert instance._correlate_trigger(item, trigger(0, "door"), start) is None
    combined = instance._correlate_trigger(
        item, trigger(1, "motion"), start + timedelta(seconds=20)
    )

    assert combined is not None
    assert [entry["trigger_index"] for entry in combined["matched_triggers"]] == [0, 1]
    assert combined["correlation"]["window_seconds"] == 30
    assert combined["correlation"]["first_trigger_at"] == start.isoformat()


def test_expired_partial_match_is_not_reused():
    instance = manager()
    item = record()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    assert instance._correlate_trigger(item, trigger(0, "door"), start) is None
    assert (
        instance._correlate_trigger(item, trigger(1, "motion"), start + timedelta(seconds=31))
        is None
    )
    combined = instance._correlate_trigger(item, trigger(0, "door"), start + timedelta(seconds=40))
    assert combined is not None
    assert (
        combined["correlation"]["first_trigger_at"] == (start + timedelta(seconds=31)).isoformat()
    )


def test_repeated_same_trigger_refreshes_its_place_in_window():
    instance = manager()
    item = record()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    instance._correlate_trigger(item, trigger(0, "door-old"), start)
    instance._correlate_trigger(item, trigger(0, "door-new"), start + timedelta(seconds=25))
    combined = instance._correlate_trigger(
        item, trigger(1, "motion"), start + timedelta(seconds=40)
    )

    assert combined is not None
    assert combined["matched_triggers"][0]["entity_id"] == "door-new"


def test_correlation_progress_can_be_cleared_on_rebuild_boundary():
    instance = manager()
    item = record()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    instance._correlate_trigger(item, trigger(0, "door"), start)

    instance._clear_correlation(item.id)

    assert (
        instance._correlate_trigger(item, trigger(1, "motion"), start + timedelta(seconds=10))
        is None
    )


@pytest.mark.asyncio
async def test_manual_trigger_bypasses_correlation(monkeypatch):
    instance = manager()
    item = record()
    instance.store = SimpleNamespace(records={item.id: item})
    base_trigger = AsyncMock()
    monkeypatch.setattr(NotificationManager, "_async_trigger", base_trigger)
    manual = {"type": "manual", "friendly_name": "Manual trigger"}

    await instance._async_trigger(item.id, item.revision, manual)

    base_trigger.assert_awaited_once_with(instance, item.id, item.revision, manual)


@pytest.mark.asyncio
async def test_match_current_state_seeds_every_correlated_state_trigger():
    instance = manager()
    item = record()
    instance.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="on", attributes={"friendly_name": entity_id}
            )
        )
    )
    instance._async_trigger = AsyncMock()

    await LifecycleNotificationManager._async_match_current(instance, item)

    assert instance._async_trigger.await_count == 2
    assert [
        call.args[2]["trigger_index"] for call in instance._async_trigger.await_args_list
    ] == [0, 1]
