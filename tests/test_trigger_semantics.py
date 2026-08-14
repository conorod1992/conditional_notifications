"""Pure transition and bounded event semantics."""

from __future__ import annotations

from custom_components.conditional_notifications.conditions import numeric_matches
from custom_components.conditional_notifications.triggers import (
    _numeric_match,
    _state_match,
    _subset,
)
from homeassistant.core import State


def state(value, **attributes):
    return State("sensor.example", value, attributes)


def test_state_requires_genuine_relevant_transition():
    definition = {"type": "state", "entity_id": "sensor.example", "to": "on"}
    assert _state_match(definition, state("off"), state("on"))
    assert not _state_match(definition, state("on"), state("on", friendly_name="Changed attribute"))
    assert not _state_match(definition, None, state("on"))
    assert not _state_match(definition, state("off"), state("unavailable"))


def test_attribute_state_transition():
    definition = {
        "type": "state",
        "entity_id": "sensor.example",
        "attribute": "mode",
        "from": "idle",
        "to": "active",
    }
    assert _state_match(definition, state("ok", mode="idle"), state("ok", mode="active"))


def test_numeric_only_matches_crossing_into_range():
    definition = {"type": "numeric_state", "entity_id": "sensor.example", "above": 10, "below": 20}
    assert _numeric_match(definition, state("5"), state("11"))[0]
    assert not _numeric_match(definition, state("11"), state("12"))[0]
    assert not _numeric_match(definition, state("unknown"), state("bad"))[0]
    assert numeric_matches(11, definition)
    assert not numeric_matches(10, definition)
    assert not numeric_matches(20, definition)


def test_event_data_is_recursive_safe_subset():
    assert _subset({"device": {"id": 1}}, {"device": {"id": 1, "name": "door"}, "extra": True})
    assert not _subset({"device": {"id": 2}}, {"device": {"id": 1}})
