"""Regression coverage for final bug-sweep robustness fixes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.manager import NotificationManager
from custom_components.conditional_notifications.models import NotificationRecord, duration_seconds
from custom_components.conditional_notifications.sensor import ConditionalNotificationsSensor
from custom_components.conditional_notifications.services import async_register_services
from custom_components.conditional_notifications.storage import NotificationStore
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)
from homeassistant.core import State, SupportsResponse


def definition(**extra):
    data = {
        "name": "Watch",
        "triggers": [{"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
        "title": "Watch",
        "message": "Matched",
    }
    data.update(extra)
    return data


class FakeRawStore:
    def __init__(self, data):
        self.data = data
        self.saved = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.saved = data


class FakeStates:
    def __init__(self, states=None):
        self.states = states or {}

    def get(self, entity_id):
        return self.states.get(entity_id)


def test_duration_and_validation_reject_pathological_values():
    with pytest.raises(ValueError, match="boolean"):
        duration_seconds(True)
    with pytest.raises(ValueError, match="finite"):
        duration_seconds(float("inf"))
    with pytest.raises(DefinitionError, match="finite number"):
        validate_definition(
            definition(
                triggers=[
                    {
                        "type": "numeric_state",
                        "entity_id": "sensor.temperature",
                        "above": float("nan"),
                    }
                ]
            )
        )
    with pytest.raises(DefinitionError, match="true or false"):
        validate_definition(definition(match_current_state="false"))
    with pytest.raises(DefinitionError, match="unsupported fields"):
        validate_definition(
            definition(active_window={"start": "08:00", "end": "18:00", "weekday": ["monday"]})
        )


def test_public_dict_uses_home_assistant_timezone(monkeypatch):
    record = NotificationRecord.create(
        definition(active_window={"start": "08:00", "end": "09:00", "weekdays": ["monday"]}),
        "u1",
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.models.dt_util.now",
        lambda: datetime(2026, 8, 31, 8, 30, tzinfo=UTC),
    )
    assert record.public_dict()["currently_active"] is True


@pytest.mark.asyncio
async def test_duplicate_ids_are_quarantined_separately():
    first = NotificationRecord.create(definition(), "u1")
    duplicate = first.as_dict()
    duplicate["name"] = "Duplicate"
    duplicate["definition"] = {**duplicate["definition"], "name": "Duplicate"}
    raw = FakeRawStore(
        {
            "records": [first.as_dict(), duplicate],
            "invalid_records": [{"id": "already_quarantined"}],
            "history": [],
        }
    )
    store = object.__new__(NotificationStore)
    store._store = raw
    store.records = {}
    store.invalid_records = []
    store.history = []
    await store.async_load()
    assert list(store.records) == [first.id]
    assert len(store.invalid_records) == 2
    await store.async_save()
    assert len(raw.saved["records"]) == 1
    assert len(raw.saved["invalid_records"]) == 2


def test_summary_sensor_does_not_expose_record_identity():
    record = NotificationRecord.create(definition(name="Private title"), "u1")
    record.last_trigger_at = "2026-08-30T12:00:00+00:00"
    manager = SimpleNamespace(store=SimpleNamespace(records={record.id: record}))
    attributes = ConditionalNotificationsSensor(manager).extra_state_attributes
    assert attributes["last_triggered_at"] == record.last_trigger_at
    assert "last_triggered" not in attributes
    assert record.id not in repr(attributes)
    assert record.name not in repr(attributes)


def test_mutating_services_support_optional_response():
    registrations = {}

    class Services:
        def async_register(self, domain, service, handler, **kwargs):
            registrations[service] = kwargs["supports_response"]

    hass = SimpleNamespace(services=Services())
    async_register_services(hass, Mock())
    assert registrations["list"] is SupportsResponse.ONLY
    assert registrations["get"] is SupportsResponse.ONLY
    for service in (
        "create",
        "update",
        "delete",
        "pause",
        "resume",
        "enable",
        "disable",
        "rearm",
        "duplicate",
        "test",
        "trigger_now",
        "fire_named_trigger",
        "clear_history",
    ):
        assert registrations[service] is SupportsResponse.OPTIONAL


def test_seed_current_for_starts_fresh_duration_proof():
    accepted = []
    scheduled = {}
    runtime = SimpleNamespace(
        schedule_duration=lambda index, seconds, action: scheduled.update(
            index=index, seconds=seconds, action=action
        )
    )
    state = State("binary_sensor.motion", "on", {"friendly_name": "Motion"})
    manager = object.__new__(NotificationManager)
    manager.hass = SimpleNamespace(states=FakeStates({state.entity_id: state}))
    manager._seed_current_duration(
        runtime,
        {"type": "state", "entity_id": state.entity_id, "to": "on", "for": 30},
        0,
        accepted.append,
    )
    assert scheduled["seconds"] == 30
    assert accepted == []
    scheduled["action"]()
    assert len(accepted) == 1
    assert accepted[0]["matched_current_state"] is True


@pytest.mark.asyncio
async def test_initialize_requests_fresh_duration_proof():
    record = NotificationRecord.create(definition(), "u1")
    store = SimpleNamespace(
        records={record.id: record},
        history=[],
        invalid_records=[],
        async_load=AsyncMock(),
        async_save=AsyncMock(),
    )
    manager = object.__new__(NotificationManager)
    manager.hass = SimpleNamespace()
    manager.options = {**DEFAULT_OPTIONS}
    manager.store = store
    manager._shutting_down = False
    manager._validate_templates = Mock()
    manager._prune_history = Mock()
    manager.async_rebuild = AsyncMock()
    await manager.async_initialize()
    manager.async_rebuild.assert_awaited_once_with(record, prove_current_durations=True)


def test_current_zone_resolution_uses_in_zones_membership():
    zone = State("zone.work", "0", {"friendly_name": "Work"})
    person = State("person.alex", "home", {"in_zones": ["zone.work"]})
    manager = object.__new__(NotificationManager)
    manager.hass = SimpleNamespace(
        states=FakeStates({zone.entity_id: zone, person.entity_id: person})
    )
    record = NotificationRecord.create(
        definition(
            resolve_when={
                "type": "zone",
                "entity_id": person.entity_id,
                "zone_entity_id": zone.entity_id,
                "event": "enter",
            }
        ),
        "u1",
    )
    record.active_occurrence = True
    context = manager._current_resolution_context(record)
    assert context is not None
    assert context["matched_current_resolution"] is True
