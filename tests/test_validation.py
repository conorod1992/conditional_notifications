"""Definition validation tests."""

from __future__ import annotations

import pytest
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)


def base(**changes):
    data = {
        "name": "Kitchen motion",
        "triggers": [{"type": "state", "entity_id": "binary_sensor.kitchen", "to": "on"}],
        "title": "Motion",
        "message": "Kitchen motion detected",
    }
    data.update(changes)
    return data


def test_minimal_definition_is_normalized():
    result = validate_definition(base())
    assert result["match"] == "any"
    assert result["repeat_policy"] == "once"
    assert result["delivery"] == {"use_defaults": True}
    assert result["notify_on_expiry"] is False


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": ""}, "name"),
        ({"triggers": []}, "triggers"),
        ({"match": "all"}, "match"),
        ({"repeat_policy": "sometimes"}, "repeat_policy"),
        ({"repeat_policy": "limited", "max_notifications": 0}, "max_notifications"),
        ({"cooldown": -1}, "cooldown"),
        ({"debounce": {"fortnights": 1}}, "debounce"),
        ({"expires_at": "2026-08-15T10:00:00"}, "expires_at"),
        ({"notify_on_expiry": True}, "notify_on_expiry"),
        ({"title": ""}, "title"),
        ({"message": ""}, "message"),
    ],
)
def test_rejects_invalid_top_level(changes, field):
    with pytest.raises(DefinitionError) as error:
        validate_definition(base(**changes))
    assert error.value.field == field


@pytest.mark.parametrize(
    "trigger",
    [
        {"type": "state", "entity_id": "sensor.x"},
        {"type": "state", "entity_id": "sensor.x", "from": "on", "to": "on"},
        {"type": "numeric_state", "entity_id": "sensor.x"},
        {"type": "numeric_state", "entity_id": "sensor.x", "above": 10, "below": 5},
        {"type": "zone", "entity_id": "person.x", "zone_entity_id": "sensor.no", "event": "enter"},
        {"type": "zone", "entity_id": "person.x", "zone_entity_id": "zone.home", "event": "arrive"},
        {"type": "event", "event_type": ""},
        {"type": "named", "trigger_id": ""},
        {"type": "template", "value_template": "true"},
    ],
)
def test_rejects_unsafe_or_incomplete_triggers(trigger):
    with pytest.raises(DefinitionError):
        validate_definition(base(triggers=[trigger]))


def test_numeric_range_and_durations_normalize():
    result = validate_definition(
        base(
            triggers=[
                {
                    "type": "numeric_state",
                    "entity_id": "sensor.freezer",
                    "above": "-10",
                    "below": "5",
                    "for": {"minutes": 5},
                }
            ],
            cooldown={"minutes": 20},
            debounce=3,
            repeat_policy="limited",
            max_notifications="3",
        )
    )
    assert result["triggers"][0]["above"] == -10.0
    assert result["triggers"][0]["for"] == 300
    assert result["cooldown"] == 1200
    assert result["max_notifications"] == 3


def test_expiry_must_follow_availability():
    with pytest.raises(DefinitionError, match="after"):
        validate_definition(
            base(available_from="2026-08-15T12:00:00+01:00", expires_at="2026-08-15T11:00:00+01:00")
        )


def test_recurring_window_and_weekdays_validate():
    result = validate_definition(
        base(
            active_window={
                "start": "22:00",
                "end": "07:00",
                "weekdays": ["monday", "monday", "tuesday"],
            }
        )
    )
    assert result["active_window"]["weekdays"] == ["monday", "tuesday"]
    with pytest.raises(DefinitionError):
        validate_definition(base(active_window={"start": "25:00", "end": "07:00"}))


def test_resolution_reuses_bounded_trigger_schema():
    result = validate_definition(
        base(resolve_when={"type": "numeric_state", "entity_id": "sensor.freezer", "below": -12})
    )
    assert result["resolve_when"]["below"] == -12
    with pytest.raises(DefinitionError):
        validate_definition(base(resolve_when={"type": "event", "event_type": "x", "for": 5}))


def test_delivery_rejects_arbitrary_fields():
    with pytest.raises(DefinitionError, match="unsupported"):
        validate_definition(base(delivery={"service": "light.turn_on"}))


def test_definition_and_trigger_reject_arbitrary_action_structures():
    with pytest.raises(DefinitionError, match="unsupported fields"):
        validate_definition(base(action={"service": "lock.unlock"}))
    with pytest.raises(DefinitionError, match="unsupported fields"):
        validate_definition(
            base(
                triggers=[
                    {
                        "type": "state",
                        "entity_id": "sensor.x",
                        "to": "on",
                        "then": {"service": "light.turn_on"},
                    }
                ]
            )
        )
