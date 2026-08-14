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
        return []


class FakeBus:
    def async_fire(self, *args, **kwargs):
        pass


class FakeStates:
    def get(self, entity_id):
        return None


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
