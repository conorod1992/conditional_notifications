"""Post-merge regression coverage for the final robustness sweep."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import State

from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.lifecycle import LifecycleNotificationManager
from custom_components.conditional_notifications.manager import NotificationManager
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.triggers import _state_match, _state_still_matches
from custom_components.conditional_notifications.validation import DefinitionError, validate_definition


class FakeStates:
    def __init__(self, states=None):
        self.states = states or {}

    def get(self, entity_id):
        return self.states.get(entity_id)


class FakeBus:
    def async_fire(self, *args, **kwargs):
        return None


class FakeStore:
    def __init__(self):
        self.records = {}
        self.history = []
        self.invalid_records = []
        self.saves = 0

    async def async_save(self):
        self.saves += 1

    def add_history(self, item, **kwargs):
        self.history.append(item)

    def history_for(self, notification_id=None):
        return []


def definition(**extra):
    data = {
        "name": "Watch",
        "triggers": [
            {
                "type": "state",
                "entity_id": "binary_sensor.motion",
                "to": "on",
            }
        ],
        "conditions": [],
        "title": "Watch",
        "message": "Matched",
        "repeat_policy": "every",
        "delivery": {"use_defaults": True},
    }
    data.update(extra)
    return data


def normalized_record(**extra):
    normalized = validate_definition(definition(**extra))
    normalized.pop("enabled", None)
    return NotificationRecord.create(normalized, "u1")


def bare_manager(cls=NotificationManager):
    manager = object.__new__(cls)
    manager.hass = SimpleNamespace(bus=FakeBus(), states=FakeStates(), data={})
    manager.options = {**DEFAULT_OPTIONS}
    manager.store = FakeStore()
    manager._runtimes = {}
    manager._locks = {}
    manager._subscribers = set()
    manager._tasks = set()
    manager._shutting_down = False
    manager._inflight_deliveries = set()
    manager._delivery_tasks = {}
    manager._pending_resolutions = {}
    manager._event = Mock()
    manager._broadcast = Mock()
    manager._validate_templates = Mock()
    return manager


def test_from_only_duration_survives_unrelated_changes_until_returning_to_from():
    trigger = {"type": "state", "entity_id": "sensor.mode", "from": "off"}
    assert _state_still_matches(trigger, State("sensor.mode", "on"))
    assert _state_still_matches(trigger, State("sensor.mode", "on", {"battery": 90}))
    assert _state_still_matches(trigger, State("sensor.mode", "idle"))
    assert not _state_still_matches(trigger, State("sensor.mode", "off"))
    assert not _state_still_matches(trigger, State("sensor.mode", "unavailable"))


def test_structured_attribute_state_values_do_not_crash_unknown_checks():
    trigger = {
        "type": "state",
        "entity_id": "sensor.mode",
        "attribute": "members",
        "from": ["a"],
        "to": ["a", "b"],
    }
    old = State("sensor.mode", "ok", {"members": ["a"]})
    new = State("sensor.mode", "ok", {"members": ["a", "b"]})
    assert _state_match(trigger, old, new)


@pytest.mark.asyncio
async def test_match_current_state_uses_attribute_value():
    manager = bare_manager()
    manager._async_trigger = AsyncMock()
    state = State("sensor.mode", "idle", {"mode": "active"})
    manager.hass.states = FakeStates({state.entity_id: state})
    record = normalized_record(
        triggers=[
            {
                "type": "state",
                "entity_id": state.entity_id,
                "attribute": "mode",
                "to": "active",
            }
        ],
        match_current_state=True,
    )

    await manager._async_match_current(record)

    manager._async_trigger.assert_awaited_once()
    context = manager._async_trigger.await_args.args[2]
    assert context["to_state"] == "active"
    assert context["attribute"] == "mode"


@pytest.mark.asyncio
async def test_correlated_match_current_state_uses_attribute_value():
    manager = bare_manager(LifecycleNotificationManager)
    manager._async_trigger = AsyncMock()
    first = State("sensor.first", "idle", {"mode": "active"})
    second = State("sensor.second", "on")
    manager.hass.states = FakeStates({first.entity_id: first, second.entity_id: second})
    record = normalized_record(
        triggers=[
            {
                "type": "state",
                "entity_id": first.entity_id,
                "attribute": "mode",
                "to": "active",
            },
            {"type": "state", "entity_id": second.entity_id, "to": "on"},
        ],
        match="all_within",
        match_window=30,
        match_current_state=True,
    )

    await manager._async_match_current(record)

    assert manager._async_trigger.await_count == 2
    first_context = manager._async_trigger.await_args_list[0].args[2]
    assert first_context["to_state"] == "active"
    assert first_context["attribute"] == "mode"


def test_numeric_duration_already_true_starts_fresh_restart_proof():
    accepted = []
    scheduled = {}
    runtime = SimpleNamespace(
        schedule_duration=lambda index, seconds, action: scheduled.update(
            index=index, seconds=seconds, action=action
        )
    )
    state = State("sensor.temperature", "12", {"friendly_name": "Temperature"})
    manager = bare_manager()
    manager.hass.states = FakeStates({state.entity_id: state})

    manager._seed_current_duration(
        runtime,
        {
            "type": "numeric_state",
            "entity_id": state.entity_id,
            "above": 10,
            "for": 45,
        },
        0,
        accepted.append,
    )

    assert scheduled["seconds"] == 45
    assert accepted == []
    scheduled["action"]()
    assert accepted[0]["type"] == "numeric_state"
    assert accepted[0]["value"] == 12
    assert accepted[0]["matched_current_state"] is True


@pytest.mark.asyncio
async def test_match_current_state_with_for_starts_fresh_proof_on_create(monkeypatch):
    manager = bare_manager()
    manager._seed_current_duration = Mock()
    manager._async_match_current = AsyncMock()
    record = normalized_record(
        triggers=[
            {
                "type": "state",
                "entity_id": "binary_sensor.motion",
                "to": "on",
                "for": 30,
            }
        ],
        match_current_state=True,
    )
    manager.store.records[record.id] = record
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.attach_trigger",
        lambda *args, **kwargs: None,
    )

    await NotificationManager.async_rebuild(manager, record, allow_current=True)

    manager._seed_current_duration.assert_called_once()
    manager._async_match_current.assert_awaited_once_with(record)


def test_validation_rejects_malformed_entity_ids_and_scalar_state_types():
    with pytest.raises(DefinitionError, match="valid entity ID"):
        validate_definition(
            definition(
                triggers=[
                    {
                        "type": "state",
                        "entity_id": "binary sensor.motion",
                        "to": "on",
                    }
                ]
            )
        )
    with pytest.raises(DefinitionError, match="string"):
        validate_definition(
            definition(
                triggers=[
                    {
                        "type": "state",
                        "entity_id": "binary_sensor.motion",
                        "to": True,
                    }
                ]
            )
        )
    with pytest.raises(DefinitionError, match="valid entity ID"):
        validate_definition(
            definition(
                delivery={
                    "use_defaults": False,
                    "notify_entities": ["notify.bad id"],
                }
            )
        )

    result = validate_definition(
        definition(
            triggers=[
                {
                    "type": "state",
                    "entity_id": "sensor.mode",
                    "attribute": "members",
                    "to": ["a", "b"],
                }
            ]
        )
    )
    assert result["triggers"][0]["to"] == ["a", "b"]


@pytest.mark.asyncio
async def test_delete_clears_integration_owned_persistent_notification(monkeypatch):
    manager = bare_manager()
    record = normalized_record()
    record.active_occurrence = True
    manager.store.records[record.id] = record
    clear = Mock()
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", clear)

    await manager.async_delete(record)

    clear.assert_called_once_with(manager.hass, record.id)


@pytest.mark.asyncio
async def test_semantic_edit_clears_abandoned_active_notification(monkeypatch):
    manager = bare_manager()
    manager.async_rebuild = AsyncMock()
    record = normalized_record(
        resolve_when={
            "type": "state",
            "entity_id": "binary_sensor.motion",
            "to": "off",
        }
    )
    record.active_occurrence = True
    record.status = "active"
    manager.store.records[record.id] = record
    clear = Mock()
    monkeypatch.setattr("custom_components.conditional_notifications.manager.async_clear", clear)

    await manager.async_update(
        record,
        {
            "triggers": [
                {
                    "type": "state",
                    "entity_id": "binary_sensor.door",
                    "to": "on",
                }
            ]
        },
    )

    assert not record.active_occurrence
    clear.assert_called_once_with(manager.hass, record.id)
