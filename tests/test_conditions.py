"""Bounded condition evaluation tests."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.conditional_notifications.conditions import async_evaluate_conditions
from homeassistant.core import State


class States:
    def __init__(self, values):
        self.values = values

    def get(self, entity_id):
        return self.values.get(entity_id)


class Hass:
    def __init__(self, values):
        self.states = States(values)


def test_multiple_conditions_use_and_semantics():
    hass = Hass(
        {
            "person.conor": State("person.conor", "not_home"),
            "sensor.temp": State("sensor.temp", "12"),
        }
    )
    passed, details = async_evaluate_conditions(
        hass,
        [
            {"type": "state", "entity_id": "person.conor", "state": "not_home"},
            {"type": "numeric_state", "entity_id": "sensor.temp", "above": 10, "below": 20},
        ],
        datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    assert passed
    assert all(item["passed"] for item in details)


def test_false_condition_stops_evaluation():
    hass = Hass(
        {"person.conor": State("person.conor", "home"), "sensor.temp": State("sensor.temp", "12")}
    )
    passed, details = async_evaluate_conditions(
        hass,
        [
            {"type": "state", "entity_id": "person.conor", "state": "not_home"},
            {"type": "numeric_state", "entity_id": "sensor.temp", "above": 10},
        ],
        datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    assert not passed
    assert len(details) == 1


def test_time_condition_supports_overnight():
    passed, _ = async_evaluate_conditions(
        Hass({}),
        [{"type": "time", "after": "22:00", "before": "07:00"}],
        datetime(2026, 8, 14, 23, tzinfo=UTC),
    )
    assert passed


def test_unknown_state_never_passes_even_when_negated():
    passed, _ = async_evaluate_conditions(
        Hass({"sensor.x": State("sensor.x", "unavailable")}),
        [{"type": "state", "entity_id": "sensor.x", "state": "home", "negate": True}],
        datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    assert not passed


def test_zone_condition_uses_current_in_zones_attribute():
    hass = Hass(
        {
            "zone.home": State("zone.home", "0", {"latitude": 1, "longitude": 1, "radius": 100}),
            "person.conor": State("person.conor", "home", {"in_zones": ["zone.home"]}),
        }
    )
    passed, details = async_evaluate_conditions(
        hass,
        [{"type": "zone", "entity_id": "person.conor", "zone_entity_id": "zone.home"}],
        datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    assert passed and details[0]["passed"]
