"""Regression tests for subtle lifecycle, timing, and persistence edge cases."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.conditional_notifications.conditions import async_evaluate_conditions
from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.manager import NotificationManager
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.storage import NotificationStore
from custom_components.conditional_notifications.validation import DefinitionError, validate_definition


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[str, NotificationRecord] = {}
        self.invalid_records: list[dict] = []
        self.history = []
        self.saves = 0

    async def async_load(self) -> None:
        return None

    async def async_save(self) -> None:
        self.saves += 1

    def add_history(self, item, **kwargs) -> None:
        self.history.append(item)

    def history_for(self, notification_id=None):
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


def test_overnight_time_condition_uses_starting_weekday() -> None:
    passed, results = async_evaluate_conditions(
        FakeHass(),
        [
            {
                "type": "time",
                "after": "22:00",
                "before": "07:00",
                "weekdays": ["monday"],
            }
        ],
        datetime(2026, 8, 25, 1, 0, tzinfo=UTC),
    )

    assert passed
    assert results[0]["passed"]


def test_time_condition_weekdays_are_strictly_validated() -> None:
    with pytest.raises(DefinitionError, match="non-empty list of valid weekdays"):
        validate_definition(
            definition(
                conditions=[
                    {
                        "type": "time",
                        "after": "22:00",
                        "before": "07:00",
                        "weekdays": "monday",
                    }
                ]
            )
        )

    with pytest.raises(DefinitionError, match="non-empty list of valid weekdays"):
        validate_definition(
            definition(
                conditions=[
                    {
                        "type": "time",
                        "after": "22:00",
                        "before": "07:00",
                        "weekdays": ["monday", "nonsense"],
                    }
                ]
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "extra"),
    [("once", {}), ("limited", {"max_notifications": 2})],
)
async def test_total_delivery_failure_does_not_consume_occurrence(
    manager, monkeypatch, policy, extra
) -> None:
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(return_value="rendered"),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"channel": "notify.phone", "success": False, "error": "offline"}]),
    )
    record = NotificationRecord.create(definition(policy=policy, **extra), "u1")
    manager.store.records[record.id] = record

    await manager._async_trigger(record.id, record.revision, {"type": "manual"})

    assert record.notification_count == 0
    assert record.enabled
    assert record.status == "watching"
    assert record.last_accepted_at is None
    assert record.qualifying_match_seen
    if policy == "limited":
        assert record.remaining() == 2


@pytest.mark.asyncio
async def test_template_failure_does_not_consume_once_occurrence(manager, monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_render",
        AsyncMock(side_effect=ValueError("render failed")),
    )
    deliver = AsyncMock()
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    record = NotificationRecord.create(definition(), "u1")
    manager.store.records[record.id] = record

    await manager._async_trigger(record.id, record.revision, {"type": "manual"})

    assert record.notification_count == 0
    assert record.enabled and record.status == "watching"
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_ignore_is_saved_and_broadcast(manager) -> None:
    manager._broadcast = Mock()
    record = NotificationRecord.create(definition(policy="every", cooldown=3600), "u1")
    record.last_accepted_at = datetime.now(UTC).isoformat()
    manager.store.records[record.id] = record

    await manager._async_trigger(record.id, record.revision, {"type": "manual"})

    assert record.last_ignored_reason == "Cooldown is still active"
    assert manager.store.saves == 1
    manager._broadcast.assert_called_once_with("ignored", record, record.id)


@pytest.mark.asyncio
async def test_resolved_title_is_rendered(manager, monkeypatch) -> None:
    render = AsyncMock(side_effect=["Rendered title", "Rendered message"])
    deliver = AsyncMock(return_value=[{"channel": "test", "success": True}])
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_render", render)
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", deliver
    )
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", Mock())
    record = NotificationRecord.create(
        definition(
            policy="every",
            resolve_when={"type": "state", "entity_id": "binary_sensor.motion", "to": "off"},
            resolved_title="Resolved {{ trigger.type }}",
            resolved_message="All clear {{ trigger.type }}",
        ),
        "u1",
    )
    record.active_occurrence = True
    record.status = "active"
    record.notification_count = 1
    manager.store.records[record.id] = record

    await manager._async_resolve(record.id, record.revision, {"type": "state"})

    assert render.await_count == 2
    assert render.await_args_list[0].args[1] == "Resolved {{ trigger.type }}"
    assert deliver.await_args.args[2] == "Rendered title"


def test_resolved_title_template_is_validated(manager) -> None:
    manager._validate_templates = NotificationManager._validate_templates.__get__(manager)
    with pytest.raises(DefinitionError):
        manager._validate_templates(
            validate_definition(definition(resolved_title="{{ broken", resolved_message="ok"))
        )


@pytest.mark.asyncio
async def test_deleted_record_is_not_rebuilt_after_resolution_delivery(manager, monkeypatch) -> None:
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
    record = NotificationRecord.create(
        definition(
            policy="every",
            resolve_when={"type": "state", "entity_id": "binary_sensor.motion", "to": "off"},
            resolved_message="Resolved",
        ),
        "u1",
    )
    record.active_occurrence = True
    record.status = "active"
    record.notification_count = 1
    manager.store.records[record.id] = record

    task = asyncio.create_task(manager._async_resolve(record.id, record.revision, {"type": "state"}))
    await started.wait()
    await manager.async_delete(record)
    finish.set()
    await task

    manager.async_rebuild.assert_not_awaited()
    manager._event.assert_not_called()
    assert record.id not in manager._runtimes


@pytest.mark.asyncio
async def test_deleted_record_gets_no_late_expiry_completion(manager, monkeypatch) -> None:
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
    record = NotificationRecord.create(
        definition(expires_at="2099-01-01T00:00:00+00:00", notify_on_expiry=True), "u1"
    )
    manager.store.records[record.id] = record

    task = asyncio.create_task(manager._async_expire(record.id, record.revision))
    await started.wait()
    await manager.async_delete(record)
    finish.set()
    await task

    assert manager.store.history[-1].event == "deleted"
    manager._event.assert_not_called()


@pytest.mark.asyncio
async def test_stale_or_deleted_record_cannot_be_rebuilt() -> None:
    instance = object.__new__(NotificationManager)
    instance.hass = FakeHass()
    instance.options = {**DEFAULT_OPTIONS}
    instance.store = FakeStore()
    instance._runtimes = {}
    instance._locks = {}
    instance._subscribers = set()
    record = NotificationRecord.create(definition(), "u1")

    await NotificationManager.async_rebuild(instance, record)

    assert instance._runtimes == {}


@pytest.mark.asyncio
async def test_invalid_persisted_definition_is_quarantined(manager) -> None:
    valid = NotificationRecord.create(definition("Valid"), "u1")
    invalid = NotificationRecord.create(definition("Invalid"), "u1")
    invalid.definition = {"name": "Invalid", "title": "x", "message": "y"}
    manager.store.records = {valid.id: valid, invalid.id: invalid}

    await manager.async_initialize()

    assert valid.id in manager.store.records
    assert invalid.id not in manager.store.records
    assert manager.store.invalid_records[0]["id"] == invalid.id
    assert manager.store.saves == 1
    manager.async_rebuild.assert_awaited_once_with(valid)


@pytest.mark.asyncio
async def test_malformed_top_level_record_does_not_abort_storage_load() -> None:
    store = object.__new__(NotificationStore)
    store._store = AsyncMock()
    store._store.async_load.return_value = {
        "records": [
            {"id": "broken"},
            "not-an-object",
        ],
        "history": [],
    }

    await store.async_load()

    assert store.records == {}
    assert store.invalid_records == [{"id": "broken"}]
