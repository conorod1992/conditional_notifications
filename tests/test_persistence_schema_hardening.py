"""Regression coverage for persistence, schema, and entity lifecycle hardening."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.models import (
    NotificationRecord,
    parse_datetime,
)
from custom_components.conditional_notifications.storage import NotificationStore
from custom_components.conditional_notifications.triggers import (
    RuntimeSubscriptions,
    attach_trigger,
)
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)
from homeassistant.core import State


def definition(**changes):
    data = {
        "name": "Door",
        "triggers": [{"type": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "title": "Door",
        "message": "Door opened",
    }
    data.update(changes)
    return data


@pytest.mark.parametrize("value", [[], {}, 0, False])
def test_parse_datetime_rejects_non_string_falsey_values(value) -> None:
    with pytest.raises(ValueError, match="timestamp string"):
        parse_datetime(value)


def test_parse_datetime_wraps_invalid_iso_values() -> None:
    with pytest.raises(ValueError, match="valid ISO timestamp"):
        parse_datetime("definitely-not-a-date")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_id", []),
        ("created_at", 123),
        ("updated_at", "not-a-date"),
        ("definition", []),
        ("semantic_key", {}),
        ("description", []),
        ("enabled", 1),
        ("paused", "false"),
        ("revision", True),
        ("revision", 0),
        ("notification_count", "1"),
        ("notification_count", -1),
        ("qualifying_match_seen", 0),
        ("last_accepted_at", []),
        ("last_trigger_at", "not-a-date"),
        ("last_trigger", []),
        ("last_ignored_reason", {}),
        ("last_delivery", {}),
        ("last_delivery", ["bad"]),
        ("active_occurrence", 1),
    ],
)
def test_persisted_record_envelope_rejects_wrong_runtime_types(field, value) -> None:
    record = NotificationRecord.create(definition(), "user-1")
    data = record.as_dict()
    data[field] = value

    with pytest.raises(ValueError):
        NotificationRecord.from_dict(data)


def test_persisted_record_envelope_accepts_legacy_missing_owner() -> None:
    record = NotificationRecord.create(definition(), None)
    data = record.as_dict()
    data.pop("owner_id")

    restored = NotificationRecord.from_dict(data)

    assert restored.owner_id is None


@pytest.mark.asyncio
async def test_storage_quarantines_malformed_record_envelope() -> None:
    record = NotificationRecord.create(definition(), "user-1")
    malformed = record.as_dict()
    malformed["notification_count"] = "1"
    store = object.__new__(NotificationStore)
    store._store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={"records": [malformed], "invalid_records": [], "history": []}
        )
    )

    await store.async_load()

    assert store.records == {}
    assert store.invalid_records == [malformed]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": ["Door"]}, "name"),
        ({"semantic_key": ["door"]}, "semantic_key"),
        ({"description": {"text": "Door"}}, "description"),
        ({"available_from": []}, "available_from"),
        ({"expires_at": {}}, "expires_at"),
        ({"active_window": []}, "active_window"),
        ({"resolve_when": []}, "resolve_when"),
    ],
)
def test_falsey_or_optional_fields_do_not_bypass_schema(changes, field) -> None:
    with pytest.raises(DefinitionError) as err:
        validate_definition(definition(**changes))

    assert err.value.field == field


def test_empty_optional_temporal_structures_normalize_to_absent() -> None:
    result = validate_definition(
        definition(
            available_from="",
            expires_at=None,
            active_window={},
            resolve_when=None,
        )
    )

    assert "available_from" not in result
    assert "expires_at" not in result
    assert "active_window" not in result
    assert "resolve_when" not in result


@pytest.mark.parametrize(
    "companion",
    [
        {"actions": [{"title": ["Open"], "action": "OPEN"}]},
        {"actions": [{"title": "Open", "action": 123}]},
        {"actions": [{"title": "Open", "uri": ["/lovelace"]}]},
    ],
)
def test_companion_optional_strings_are_not_coerced(companion) -> None:
    with pytest.raises(DefinitionError):
        validate_definition(
            definition(
                delivery={
                    "use_defaults": False,
                    "persistent_notification": False,
                    "companion": companion,
                }
            )
        )


def test_state_listener_matches_entity_creation_and_removal(monkeypatch) -> None:
    listeners = []

    def track(_hass, _entity_ids, listener):
        listeners.append(listener)
        return lambda: None

    monkeypatch.setattr(
        "custom_components.conditional_notifications.triggers.async_track_state_change_event",
        track,
    )
    hass = SimpleNamespace()

    created = []
    runtime = RuntimeSubscriptions(hass, "created", 1)
    attach_trigger(
        runtime,
        {"type": "state", "entity_id": "binary_sensor.door", "to": "on"},
        0,
        created.append,
    )
    listeners[-1](
        SimpleNamespace(
            data={
                "old_state": None,
                "new_state": State("binary_sensor.door", "on"),
            }
        )
    )
    assert len(created) == 1
    assert created[0]["from_state"] is None
    assert created[0]["to_state"] == "on"

    removed = []
    runtime = RuntimeSubscriptions(hass, "removed", 1)
    attach_trigger(
        runtime,
        {"type": "state", "entity_id": "binary_sensor.door", "from": "on"},
        0,
        removed.append,
    )
    listeners[-1](
        SimpleNamespace(
            data={
                "old_state": State("binary_sensor.door", "on"),
                "new_state": None,
            }
        )
    )
    assert len(removed) == 1
    assert removed[0]["from_state"] == "on"
    assert removed[0]["to_state"] is None
