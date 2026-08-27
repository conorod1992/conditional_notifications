"""Resolution-duration and explicit re-arm lifecycle tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

from homeassistant.util import dt as dt_util
import pytest

from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.lifecycle import LifecycleNotificationManager
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)


class FakeStore:
    def __init__(self) -> None:
        self.records = {}
        self.history = []
        self.saves = 0

    async def async_save(self) -> None:
        self.saves += 1

    def add_history(self, item, **kwargs) -> None:
        self.history.append(item)


class FakeBus:
    def async_fire(self, *args, **kwargs) -> None:
        pass


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()


class FakeRuntime:
    def __init__(self, revision: int) -> None:
        self.revision = revision
        self.scheduled = None

    def schedule_duration(self, index: int, seconds: float, action) -> None:
        self.scheduled = (index, seconds, action)


@pytest.fixture
def manager(monkeypatch):
    instance = object.__new__(LifecycleNotificationManager)
    instance.hass = FakeHass()
    instance.options = {**DEFAULT_OPTIONS}
    instance.store = FakeStore()
    instance._runtimes = {}
    instance._locks = {}
    instance._subscribers = set()
    instance.async_rebuild = AsyncMock()
    instance._event = Mock()
    monkeypatch.setattr(
        "custom_components.conditional_notifications.lifecycle.async_clear",
        Mock(),
    )
    return instance


def definition(**extra):
    data = {
        "name": "Freezer warning",
        "triggers": [
            {
                "type": "numeric_state",
                "entity_id": "sensor.freezer_temperature",
                "above": -10,
            }
        ],
        "title": "Freezer warm",
        "message": "The freezer is too warm.",
        "repeat_policy": "once",
        "resolve_when": {
            "type": "numeric_state",
            "entity_id": "sensor.freezer_temperature",
            "below": -12,
            "for": 300,
        },
    }
    data.update(extra)
    return data


def test_resolution_duration_is_accepted_and_normalized():
    normalized = validate_definition(definition())
    assert normalized["resolve_when"]["for"] == 300

    state_definition = definition(
        resolve_when={
            "type": "state",
            "entity_id": "binary_sensor.door",
            "to": "off",
            "for": {"minutes": 5},
        }
    )
    assert validate_definition(state_definition)["resolve_when"]["for"] == 300


@pytest.mark.asyncio
async def test_current_resolution_with_duration_is_scheduled_not_immediate(manager):
    record = NotificationRecord.create(validate_definition(definition()), "u1")
    record.active_occurrence = True
    manager.store.records[record.id] = record
    runtime = FakeRuntime(record.revision)
    manager._runtimes[record.id] = runtime
    manager._current_resolution_context = Mock(return_value={"type": "numeric_state"})
    manager._async_resolve = AsyncMock()

    await manager._async_resolve_if_current(record)

    assert runtime.scheduled is not None
    index, seconds, _ = runtime.scheduled
    assert index == len(record.definition["triggers"])
    assert seconds == 300
    manager._async_resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_resolution_without_duration_remains_immediate(manager):
    no_duration = definition()
    no_duration["resolve_when"].pop("for")
    record = NotificationRecord.create(validate_definition(no_duration), "u1")
    record.active_occurrence = True
    manager.store.records[record.id] = record
    manager._current_resolution_context = Mock(return_value={"type": "numeric_state"})
    manager._async_resolve = AsyncMock()

    await manager._async_resolve_if_current(record)

    manager._async_resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_duration_completion_rechecks_resolution_condition(manager):
    record = NotificationRecord.create(validate_definition(definition()), "u1")
    record.active_occurrence = True
    manager.store.records[record.id] = record
    manager._async_resolve = AsyncMock()
    manager._current_resolution_context = Mock(return_value=None)

    await manager._async_resolve_after_duration(record.id, record.revision)
    manager._async_resolve.assert_not_awaited()

    context = {"type": "numeric_state"}
    manager._current_resolution_context = Mock(return_value=context)
    await manager._async_resolve_after_duration(record.id, record.revision)

    manager._async_resolve.assert_awaited_once()
    assert context["resolution_duration_elapsed"] is True


@pytest.mark.asyncio
async def test_rearm_resets_progress_and_starts_fresh_cycle(manager, monkeypatch):
    normalized = validate_definition(definition())
    record = NotificationRecord.create(normalized, "u1")
    record.notification_count = 4
    record.qualifying_match_seen = True
    record.last_accepted_at = dt_util.now().isoformat()
    record.last_trigger_at = dt_util.now().isoformat()
    record.last_trigger = {"type": "numeric_state"}
    record.last_ignored_reason = "Cooldown is still active"
    record.last_delivery = [{"channel": "notify.phone", "success": True}]
    record.active_occurrence = True
    record.enabled = False
    record.paused = True
    record.status = "disabled"
    manager.store.records[record.id] = record
    previous_revision = record.revision
    clear = Mock()
    monkeypatch.setattr(
        "custom_components.conditional_notifications.lifecycle.async_clear",
        clear,
    )

    result = await manager.async_rearm(record)

    assert result["status"] == "watching"
    assert record.revision == previous_revision + 1
    assert record.enabled and not record.paused
    assert record.notification_count == 0
    assert not record.qualifying_match_seen
    assert record.last_accepted_at is None
    assert record.last_trigger_at is None
    assert record.last_trigger is None
    assert record.last_ignored_reason is None
    assert record.last_delivery == []
    assert not record.active_occurrence
    assert manager.store.history[-1].event == "rearmed"
    clear.assert_called_once_with(manager.hass, record.id)
    manager.async_rebuild.assert_awaited_once_with(record)
    manager._event.assert_called_once_with("rearmed", record)


@pytest.mark.asyncio
async def test_rearm_refuses_past_absolute_expiry_without_mutating(manager):
    past = (dt_util.now() - timedelta(minutes=1)).isoformat()
    record = NotificationRecord.create(validate_definition(definition(expires_at=past)), "u1")
    record.notification_count = 2
    record.enabled = False
    record.status = "expired"
    manager.store.records[record.id] = record
    revision = record.revision

    with pytest.raises(DefinitionError, match="edit the expiry"):
        await manager.async_rearm(record)

    assert record.revision == revision
    assert record.notification_count == 2
    assert not record.enabled
    assert record.status == "expired"
    manager.async_rebuild.assert_not_awaited()
