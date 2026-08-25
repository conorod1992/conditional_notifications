"""Lifecycle, persistence ordering, repeat, race, and ownership tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.manager import (
    AmbiguousReference,
    NotFound,
    NotificationManager,
)
from custom_components.conditional_notifications.models import NotificationRecord


class FakeStore:
    def __init__(self):
        self.records = {}
        self.history = []
        self.saves = 0

    async def async_save(self):
        self.saves += 1

    def add_history(self, item, **kwargs):
        self.history.append(item)

    def history_for(self, notification_id=None):
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


class FakeBus:
    def async_fire(self, *args, **kwargs):
        pass


class FakeStates:
    def __init__(self, states=None):
        self.states = states or {}

    def get(self, entity_id):
        return self.states.get(entity_id)


class FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class FakeHass:
    def __init__(self):
        self.bus = FakeBus()
        self.states = FakeStates()
        self.data = {}


@pytest.fixture
def manager(monkeypatch):
    instance = object.__new__(NotificationManager)
    instance.hass = FakeHass()
    instance.options = {**DEFAULT_OPTIONS}
    instance.store = FakeStore()
    instance._runtimes = {}
    instance._locks = {}
    instance._subscribers = set()
    instance.async_rebuild = AsyncMock()
    instance._event = Mock()
    instance._validate_templates = Mock()
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_dispatcher_send",
        lambda *args: None,
    )
    return instance


def definition(name="Motion", policy="once", **extra):
    data = {
        "name": name,
        "triggers": [{"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
        "title": "Motion",
        "message": "Detected",
        "repeat_policy": policy,
    }
    data.update(extra)
    return data


@pytest.mark.asyncio
async def test_crud_pause_resume_and_duplicate(manager):
    created = await manager.async_create(definition(), "u1")
    record = manager.store.records[created["id"]]
    assert record.owner_id == "u1"
    updated = await manager.async_update(record, {"name": "Renamed", "cooldown": 60})
    assert updated["name"] == "Renamed" and updated["definition"]["cooldown"] == 60
    assert (await manager.async_set_paused(record, True))["status"] == "paused"
    assert (await manager.async_set_paused(record, False))["status"] == "watching"
    copy = await manager.async_duplicate(record, "u1")
    assert copy["id"] != record.id and copy["name"] == "Renamed copy"
    deleted = await manager.async_delete(record)
    assert deleted["deleted"] and record.id not in manager.store.records
    assert manager.store.saves >= 6


@pytest.mark.asyncio
async def test_presentation_edit_preserves_runtime_progress(manager):
    created = await manager.async_create(definition(policy="every"), "u1")
    record = manager.store.records[created["id"]]
    record.notification_count = 3
    record.qualifying_match_seen = True
    record.last_accepted_at = "2026-01-01T00:00:00+00:00"
    record.last_trigger_at = "2026-01-01T00:00:01+00:00"
    record.last_trigger = {"type": "state"}
    record.last_delivery = [{"success": True}]
    record.active_occurrence = True
    record.status = "active"

    await manager.async_update(
        record,
        {"name": "Renamed", "message": "New copy", "resolved_message": "Resolved copy"},
    )

    assert record.notification_count == 3
    assert record.qualifying_match_seen
    assert record.last_accepted_at is not None
    assert record.last_trigger is not None
    assert record.last_delivery == [{"success": True}]
    assert record.active_occurrence and record.status == "active"
    assert manager.store.history[-1].details == {"runtime_reset": False}


@pytest.mark.asyncio
async def test_changing_triggers_resets_previous_matches(manager):
    created = await manager.async_create(definition(policy="every"), "u1")
    record = manager.store.records[created["id"]]
    record.notification_count = 2
    record.qualifying_match_seen = True
    record.last_trigger = {"entity_id": "binary_sensor.motion"}

    await manager.async_update(
        record,
        {"triggers": [{"type": "state", "entity_id": "binary_sensor.door", "to": "on"}]},
    )

    assert record.notification_count == 0
    assert not record.qualifying_match_seen
    assert record.last_trigger is None


@pytest.mark.asyncio
async def test_changing_limited_definition_rearms_exhausted_watch(manager):
    created = await manager.async_create(definition(policy="limited", max_notifications=1), "u1")
    record = manager.store.records[created["id"]]
    record.notification_count = 1
    record.enabled = False
    record.status = "disabled"

    await manager.async_update(record, {"max_notifications": 2})

    assert record.notification_count == 0
    assert record.enabled and record.status == "watching"
    assert record.remaining() == 2


@pytest.mark.asyncio
async def test_changing_to_no_event_expiry_forgets_old_qualifying_event(manager):
    created = await manager.async_create(definition(policy="every"), "u1")
    record = manager.store.records[created["id"]]
    record.qualifying_match_seen = True

    await manager.async_update(
        record,
        {"expires_at": "2099-01-01T00:00:00+00:00", "notify_on_expiry": True},
    )

    assert not record.qualifying_match_seen


@pytest.mark.asyncio
async def test_semantic_edit_rearms_an_expired_watch(manager):
    created = await manager.async_create(definition(expires_at="2026-01-01T00:00:00+00:00"), "u1")
    record = manager.store.records[created["id"]]
    record.status = "expired"
    record.enabled = False

    await manager.async_update(record, {"expires_at": "2099-01-01T00:00:00+00:00"})

    assert record.enabled and record.status == "watching"


@pytest.mark.asyncio
async def test_semantic_edit_clears_cooldown_and_active_resolution(manager):
    created = await manager.async_create(
        definition(
            policy="every",
            cooldown=3600,
            resolve_when={"type": "state", "entity_id": "binary_sensor.motion", "to": "off"},
        ),
        "u1",
    )
    record = manager.store.records[created["id"]]
    record.last_accepted_at = "2099-01-01T00:00:00+00:00"
    record.last_trigger_at = "2099-01-01T00:00:00+00:00"
    record.active_occurrence = True
    record.status = "active"

    await manager.async_update(
        record,
        {"resolve_when": {"type": "state", "entity_id": "binary_sensor.door", "to": "off"}},
    )

    assert record.last_accepted_at is None
    assert record.last_trigger_at is None
    assert not record.active_occurrence
    assert record.status == "watching"


def test_owner_isolation_admin_and_safe_resolution(manager):
    first = NotificationRecord.create(definition("Door"), "u1")
    second = NotificationRecord.create(definition("Door"), "u1")
    other = NotificationRecord.create(definition("Private"), "u2")
    manager.store.records = {r.id: r for r in (first, second, other)}
    assert len(manager.list_records("u1", False)) == 2
    assert len(manager.list_records("admin", True)) == 3
    with pytest.raises(AmbiguousReference) as error:
        manager.resolve("Door", "u1", False)
    assert len(error.value.candidates) == 2
    with pytest.raises(NotFound):
        manager.resolve(other.id, "u1", False)
    assert manager.resolve(other.id, "admin", True) is other


@pytest.mark.asyncio
async def test_once_is_durably_disabled_before_delivery(manager, monkeypatch):
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record
    observed = []

    async def deliver(*args, **kwargs):
        observed.append((record.notification_count, record.enabled, manager.store.saves))
        return [{"channel": "test", "success": True}]

    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(side_effect=["Title", "Message"]),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    await manager._async_trigger(record.id, record.revision, {"type": "state", "timestamp": "now"})
    assert observed == [(1, False, 1)]
    assert record.status == "disabled" and record.notification_count == 1


@pytest.mark.asyncio
async def test_every_and_limited_repeat_counts(manager, monkeypatch):
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="x"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"channel": "test", "success": True}]),
    )
    every = NotificationRecord.create(definition(policy="every"), "u1")
    manager.store.records[every.id] = every
    await manager._async_trigger(every.id, every.revision, {"type": "manual"})
    await manager._async_trigger(every.id, every.revision, {"type": "manual"})
    assert every.notification_count == 2 and every.enabled
    limited = NotificationRecord.create(definition(policy="limited", max_notifications=2), "u1")
    manager.store.records[limited.id] = limited
    await manager._async_trigger(limited.id, limited.revision, {"type": "manual"})
    await manager._async_trigger(limited.id, limited.revision, {"type": "manual"})
    await manager._async_trigger(limited.id, limited.revision, {"type": "manual"})
    assert limited.notification_count == 2 and not limited.enabled and limited.remaining() == 0


@pytest.mark.asyncio
async def test_cooldown_and_debounce_block_near_simultaneous_matches(manager, monkeypatch):
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="x"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"success": True}]),
    )
    record = NotificationRecord.create(definition(policy="every", cooldown=3600, debounce=30), "u1")
    manager.store.records[record.id] = record
    await asyncio.gather(
        *[manager._async_trigger(record.id, record.revision, {"type": "manual"}) for _ in range(4)]
    )
    assert record.notification_count == 1
    assert record.last_ignored_reason in {"Ignored by debounce", "Cooldown is still active"}


@pytest.mark.asyncio
async def test_auto_resolution_rearms_repeatable_record(manager, monkeypatch):
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="x"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"success": True}]),
    )
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", Mock())
    record = NotificationRecord.create(
        definition(
            policy="every",
            resolve_when={"type": "state", "entity_id": "binary_sensor.motion", "to": "off"},
        ),
        "u1",
    )
    manager.store.records[record.id] = record
    await manager._async_trigger(record.id, record.revision, {"type": "state"})
    assert record.active_occurrence and record.status == "active"
    await manager._async_resolve(record.id, record.revision, {"type": "state"})
    assert not record.active_occurrence and record.status == "watching" and record.enabled


@pytest.mark.asyncio
async def test_stale_revision_and_deleted_record_cannot_fire(manager, monkeypatch):
    deliver = AsyncMock()
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record
    await manager._async_trigger(record.id, record.revision + 1, {"type": "manual"})
    manager.store.records.pop(record.id)
    await manager._async_trigger(record.id, record.revision, {"type": "manual"})
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_event_expiry_and_prior_match_semantics(manager, monkeypatch):
    render = AsyncMock(return_value="expiry")
    deliver = AsyncMock(return_value=[{"success": True}])
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_render", render)
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    record = NotificationRecord.create(
        definition(expires_at="2099-01-01T00:00:00+00:00", notify_on_expiry=True), "u1"
    )
    manager.store.records[record.id] = record
    await manager._async_expire(record.id, record.revision)
    assert record.status == "expired" and deliver.await_count == 1
    satisfied = NotificationRecord.create(
        definition(expires_at="2099-01-01T00:00:00+00:00", notify_on_expiry=True), "u1"
    )
    satisfied.qualifying_match_seen = True
    manager.store.records[satisfied.id] = satisfied
    await manager._async_expire(satisfied.id, satisfied.revision)
    assert deliver.await_count == 1


@pytest.mark.asyncio
async def test_no_event_expiry_records_total_delivery_failure(manager, monkeypatch):
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="expiry"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"channel": "notify.phone", "success": False, "error": "offline"}]),
    )
    record = NotificationRecord.create(
        definition(expires_at="2099-01-01T00:00:00+00:00", notify_on_expiry=True), "u1"
    )
    manager.store.records[record.id] = record

    await manager._async_expire(record.id, record.revision)

    delivery_event = manager.store.history[-1]
    assert delivery_event.event == "delivery_failed"
    assert delivery_event.summary == "All no-event expiry delivery channels failed"
    assert delivery_event.details["delivery"][0]["error"] == "offline"


@pytest.mark.asyncio
async def test_deleted_history_remains_owner_aware(manager):
    created = await manager.async_create(definition(), "u1")
    record = manager.store.records[created["id"]]
    await manager.async_delete(record)

    owner_history = manager.history_for_user("u1", False, record.id)
    assert [item["event"] for item in owner_history] == ["deleted", "created"]
    assert manager.history_for_user("u2", False, record.id) == []
    assert len(manager.history_for_user("admin", True, record.id)) == 2


@pytest.mark.asyncio
async def test_active_occurrence_resolves_when_current_state_already_matches(manager):
    record = NotificationRecord.create(
        definition(
            policy="every",
            resolve_when={"type": "state", "entity_id": "binary_sensor.motion", "to": "off"},
        ),
        "u1",
    )
    record.active_occurrence = True
    record.status = "active"
    manager.store.records[record.id] = record
    manager.hass.states = FakeStates({"binary_sensor.motion": FakeState("off")})

    await manager._async_resolve_if_current(record)

    assert not record.active_occurrence
    assert record.status == "watching"
    assert manager.store.history[-1].details["trigger"]["matched_current_resolution"] is True


@pytest.mark.asyncio
async def test_resolution_template_error_is_recorded_and_bounded(manager, monkeypatch):
    error_text = "bad render " * 100
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(side_effect=ValueError(error_text)),
    )
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", Mock())
    record = NotificationRecord.create(
        definition(
            policy="every",
            resolve_when={"type": "state", "entity_id": "binary_sensor.motion", "to": "off"},
            resolved_message="{{ broken }}",
        ),
        "u1",
    )
    record.active_occurrence = True
    record.status = "active"
    manager.store.records[record.id] = record

    await manager._async_resolve(record.id, record.revision, {"type": "state"})

    failure = manager.store.history[-1]
    assert failure.event == "template_error"
    assert failure.details["phase"] == "resolution"
    assert len(failure.details["error"]) == 300


@pytest.mark.asyncio
async def test_test_delivery_does_not_mutate_count_or_history(manager, monkeypatch):
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="x"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"success": True}]),
    )
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record
    await manager.async_test(record)
    assert record.notification_count == 0 and manager.store.history == []
