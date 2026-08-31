"""Regression tests for reload and mutation concurrency hardening."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.conditional_notifications.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.conditional_notifications.delivery import async_deliver
from custom_components.conditional_notifications.lifecycle import LifecycleNotificationManager
from custom_components.conditional_notifications.manager import NotFound, RevisionConflict
from custom_components.conditional_notifications.models import NotificationRecord


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[str, NotificationRecord] = {}
        self.history = []
        self.invalid_records = []
        self.saves = 0

    async def async_save(self) -> None:
        self.saves += 1

    def add_history(self, item, **kwargs) -> None:
        self.history.append(item)

    def history_for(self, notification_id=None):
        items = self.history
        if notification_id:
            items = [item for item in items if item.notification_id == notification_id]
        return []


class FakeBus:
    def async_fire(self, *args, **kwargs) -> None:
        return None


class FakeStates:
    def __init__(self) -> None:
        self.states = {}

    def get(self, entity_id):
        return self.states.get(entity_id)


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.states = FakeStates()
        self.data = {}

    def async_create_task(self, coroutine, *, eager_start=False):
        return asyncio.create_task(coroutine)


def definition(*, resolve: bool = False) -> dict:
    data = {
        "name": "Motion",
        "triggers": [{"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
        "conditions": [],
        "title": "Motion",
        "message": "Detected",
        "repeat_policy": "every",
        "delivery": {"use_defaults": True},
    }
    if resolve:
        data["resolve_when"] = {
            "type": "state",
            "entity_id": "binary_sensor.motion",
            "to": "off",
        }
    return data


@pytest.fixture
def manager(monkeypatch):
    instance = object.__new__(LifecycleNotificationManager)
    instance.hass = FakeHass()
    instance.options = {**DEFAULT_OPTIONS}
    instance.store = FakeStore()
    instance._runtimes = {}
    instance._locks = {}
    instance._subscribers = set()
    instance._tasks = set()
    instance._shutting_down = False
    instance._inflight_deliveries = set()
    instance._delivery_tasks = {}
    instance._pending_resolutions = {}
    instance.async_rebuild = AsyncMock()
    instance._event = Mock()
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_dispatcher_send",
        lambda *args: None,
    )
    return instance


@pytest.mark.asyncio
async def test_stale_revision_update_is_rejected(manager) -> None:
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record

    with pytest.raises(RevisionConflict, match="changed while it was being edited"):
        await manager.async_update(
            record,
            {"description": "stale edit"},
            expected_revision=record.revision - 1,
        )

    assert record.description is None
    assert record.revision == 1


@pytest.mark.asyncio
async def test_waiting_mutator_cannot_modify_record_after_delete(manager) -> None:
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record
    lock = manager._lock(record.id)
    await lock.acquire()

    delete_task = asyncio.create_task(manager.async_delete(record))
    pause_task = asyncio.create_task(manager.async_set_paused(record, True))
    await asyncio.sleep(0)
    lock.release()

    assert await delete_task == {"id": record.id, "deleted": True}
    with pytest.raises(NotFound):
        await pause_task
    assert record.id not in manager.store.records
    assert [item.event for item in manager.store.history] == ["deleted"]


@pytest.mark.asyncio
async def test_waiting_trigger_does_not_deliver_after_record_is_deleted(
    manager, monkeypatch
) -> None:
    render = AsyncMock(return_value="rendered")
    deliver = AsyncMock(return_value=[{"channel": "test", "success": True}])
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_render", render)
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record
    lock = manager._lock(record.id)
    await lock.acquire()

    task = asyncio.create_task(
        manager._async_trigger(record.id, record.revision, {"type": "state"})
    )
    await asyncio.sleep(0)
    manager.store.records.pop(record.id)
    lock.release()
    await task

    render.assert_not_awaited()
    deliver.assert_not_awaited()
    assert manager.store.history == []


@pytest.mark.asyncio
async def test_waiting_resolution_has_no_side_effect_after_record_is_deleted(
    manager, monkeypatch
) -> None:
    clear = Mock()
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", clear)
    record = NotificationRecord.create(definition(resolve=True), "u1")
    record.active_occurrence = True
    record.status = "active"
    manager.store.records[record.id] = record
    lock = manager._lock(record.id)
    await lock.acquire()

    task = asyncio.create_task(
        manager._async_resolve(record.id, record.revision, {"type": "state"})
    )
    await asyncio.sleep(0)
    manager.store.records.pop(record.id)
    lock.release()
    await task

    clear.assert_not_called()
    assert record.active_occurrence
    assert record.status == "active"
    assert manager.store.history == []


@pytest.mark.asyncio
async def test_waiting_expiry_does_not_deliver_after_record_is_deleted(
    manager, monkeypatch
) -> None:
    render = AsyncMock(return_value="rendered")
    deliver = AsyncMock(return_value=[{"channel": "test", "success": True}])
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_render", render)
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    expiry_definition = definition()
    expiry_definition["notify_on_expiry"] = True
    record = NotificationRecord.create(expiry_definition, "u1")
    manager.store.records[record.id] = record
    lock = manager._lock(record.id)
    await lock.acquire()

    task = asyncio.create_task(manager._async_expire(record.id, record.revision))
    await asyncio.sleep(0)
    manager.store.records.pop(record.id)
    lock.release()
    await task

    render.assert_not_awaited()
    deliver.assert_not_awaited()
    assert record.status == "watching"
    assert manager.store.history == []


@pytest.mark.asyncio
async def test_resolution_waits_for_initial_delivery_commit(manager, monkeypatch) -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    async def deliver(*args, **kwargs):
        started.set()
        await finish.wait()
        return [{"channel": "test", "success": True}]

    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="rendered"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", Mock())
    record = NotificationRecord.create(definition(resolve=True), "u1")
    manager.store.records[record.id] = record
    manager.hass.states.states["binary_sensor.motion"] = SimpleNamespace(state="on", attributes={})

    trigger_task = asyncio.create_task(
        manager._async_trigger(record.id, record.revision, {"type": "state"})
    )
    await started.wait()

    await manager._async_resolve(record.id, record.revision, {"type": "state"})
    assert record.active_occurrence
    assert record.status == "active"

    manager.hass.states.states["binary_sensor.motion"] = SimpleNamespace(state="off", attributes={})
    finish.set()
    await trigger_task

    assert not record.active_occurrence
    events = [item.event for item in manager.store.history]
    assert events.index("notification_sent") < events.index("resolved")


@pytest.mark.asyncio
async def test_shutdown_cancels_owned_delivery_and_rolls_back_reservation(
    manager, monkeypatch
) -> None:
    started = asyncio.Event()

    async def deliver(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="rendered"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    record = NotificationRecord.create(definition(resolve=True), "u1")
    manager.store.records[record.id] = record

    task = manager._schedule_task(
        manager._async_trigger(record.id, record.revision, {"type": "state"})
    )
    assert task is not None
    await started.wait()
    assert record.notification_count == 1

    await manager.async_shutdown()

    assert task.done()
    assert record.notification_count == 0
    assert record.last_accepted_at is None
    assert not record.active_occurrence
    assert record.status == "watching"
    assert not manager._tasks


@pytest.mark.asyncio
async def test_delivery_provider_call_times_out(monkeypatch) -> None:
    async def never_returns(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "custom_components.conditional_notifications.delivery.DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )
    hass = SimpleNamespace(
        services=SimpleNamespace(async_call=never_returns),
    )
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": ["notify.phone"],
            }
        },
    )

    result = await async_deliver(hass, record, "Title", "Message", {})

    assert result[0]["success"] is False
    assert "Timed out" in result[0]["error"]


def test_reload_broadcast_uses_stable_hass_subscriber_hub(manager) -> None:
    listener = Mock()
    manager.hass.data[DOMAIN] = {"subscribers": {listener}}

    manager.broadcast_reload()

    listener.assert_called_once()
    assert listener.call_args.args[0]["event"] == "reloaded"
