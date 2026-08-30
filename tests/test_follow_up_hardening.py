"""Regression coverage for post-sweep validation, authorization, and storage gaps."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.delivery import async_deliver
from custom_components.conditional_notifications.manager import NotificationManager
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.storage import NotificationStore
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)


def definition(**extra):
    data = {
        "name": "Watch",
        "triggers": [{"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
        "conditions": [],
        "title": "Watch",
        "message": "Matched",
        "repeat_policy": "every",
        "delivery": {"use_defaults": True},
    }
    data.update(extra)
    return data


@pytest.mark.parametrize("event_type", [["bad"], {"bad": True}, "*", "state_reported"])
def test_event_trigger_rejects_unusable_listener_types(event_type):
    with pytest.raises(DefinitionError):
        validate_definition(definition(triggers=[{"type": "event", "event_type": event_type}]))


def test_named_trigger_requires_a_string_identifier():
    with pytest.raises(DefinitionError):
        validate_definition(definition(triggers=[{"type": "named", "trigger_id": ["bad"]}]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", 123),
        ("message", ["bad"]),
        ("expiry_title", {"bad": True}),
        ("expiry_message", 123),
        ("resolved_title", ["bad"]),
        ("resolved_message", {"bad": True}),
    ],
)
def test_notification_template_fields_must_be_strings(field, value):
    with pytest.raises(DefinitionError, match="string"):
        validate_definition(definition(**{field: value}))


def test_local_time_conditions_reject_timezone_offsets():
    with pytest.raises(DefinitionError, match="timezone offset"):
        validate_definition(definition(conditions=[{"type": "time", "after": "09:00+00:00"}]))


def test_recurring_windows_reject_timezone_offsets():
    with pytest.raises(DefinitionError, match="timezone offset"):
        validate_definition(definition(active_window={"start": "09:00+00:00", "end": "17:00"}))


@pytest.mark.asyncio
async def test_user_owned_delivery_preserves_context_and_blocks_legacy_services():
    async_call = AsyncMock()
    hass = SimpleNamespace(
        auth=SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
        ),
        services=SimpleNamespace(async_call=async_call),
    )
    record = SimpleNamespace(
        id="record-id",
        owner_id="user-1",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": ["notify.phone"],
                "notify_services": ["notify.mobile_app_phone"],
            }
        },
    )

    results = await async_deliver(hass, record, "Door", "Opened", {})

    async_call.assert_awaited_once()
    call = async_call.await_args
    assert call.args[:3] == (
        "notify",
        "send_message",
        {"title": "Door", "message": "Opened"},
    )
    assert call.kwargs["blocking"] is True
    assert call.kwargs["target"] == {"entity_id": "notify.phone"}
    assert call.kwargs["context"].user_id == "user-1"
    assert results == [
        {"channel": "notify.phone", "success": True},
        {
            "channel": "notify.mobile_app_phone",
            "success": False,
            "error": "Legacy notify services require an administrator-owned notification",
        },
    ]


@pytest.mark.asyncio
async def test_admin_owned_legacy_delivery_keeps_user_context():
    async_call = AsyncMock()
    hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))),
        services=SimpleNamespace(async_call=async_call),
    )
    record = SimpleNamespace(
        id="record-id",
        owner_id="admin-1",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_services": ["notify.mobile_app_phone"],
            }
        },
    )

    results = await async_deliver(hass, record, "Door", "Opened", {})

    async_call.assert_awaited_once()
    assert async_call.await_args.kwargs["context"].user_id == "admin-1"
    assert results == [{"channel": "notify.mobile_app_phone", "success": True}]


@pytest.mark.asyncio
async def test_malformed_history_timestamp_is_dropped_during_load():
    backing = SimpleNamespace(
        async_load=AsyncMock(
            return_value={
                "records": [],
                "history": [
                    {
                        "id": "bad",
                        "notification_id": "n1",
                        "timestamp": "not-a-timestamp",
                        "event": "created",
                        "summary": "Bad",
                        "details": {},
                        "owner_id": None,
                    },
                    {
                        "id": "good",
                        "notification_id": "n1",
                        "timestamp": "2026-08-30T18:00:00+00:00",
                        "event": "created",
                        "summary": "Good",
                        "details": {},
                        "owner_id": None,
                    },
                ],
            }
        )
    )
    store = object.__new__(NotificationStore)
    store._store = backing

    await store.async_load()

    assert [item.id for item in store.history] == ["good"]


class FakeStore:
    def __init__(self, record):
        self.records = {record.id: record}
        self.invalid_records = []
        self.history = []
        self.saved = 0

    async def async_load(self):
        return None

    async def async_save(self):
        self.saved += 1

    def add_history(self, item, **kwargs):
        self.history.append(item)


@pytest.mark.asyncio
async def test_persisted_bad_event_type_is_quarantined_without_rebuild():
    bad_definition = definition(triggers=[{"type": "event", "event_type": ["bad"]}])
    record = NotificationRecord.create(bad_definition, "user-1")
    manager = object.__new__(NotificationManager)
    manager.options = dict(DEFAULT_OPTIONS)
    manager.store = FakeStore(record)
    manager._runtimes = {}
    manager._locks = {}
    manager._subscribers = set()
    manager._tasks = set()
    manager._shutting_down = False
    manager._inflight_deliveries = set()
    manager._delivery_tasks = {}
    manager._pending_resolutions = {}

    await manager.async_initialize()

    assert record.id not in manager.store.records
    assert manager.store.invalid_records[0]["id"] == record.id
    assert manager.store.saved == 1
