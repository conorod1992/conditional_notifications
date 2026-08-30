"""Regression tests for zone membership and fail-closed conditions."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.conditional_notifications.conditions import async_evaluate_conditions
from custom_components.conditional_notifications.triggers import _zone_match
from homeassistant.core import State


class FakeStates:
    def __init__(self, states: dict[str, State] | None = None) -> None:
        self.states = states or {}

    def get(self, entity_id: str) -> State | None:
        return self.states.get(entity_id)


class FakeHass:
    def __init__(self, states: dict[str, State] | None = None) -> None:
        self.states = FakeStates(states)


def _zone() -> State:
    return State(
        "zone.work",
        "0",
        {
            "friendly_name": "Work",
            "latitude": 52.836,
            "longitude": -6.934,
            "radius": 100,
        },
    )


def test_zone_match_uses_in_zones_even_when_primary_state_does_not_change() -> None:
    hass = FakeHass({"zone.work": _zone()})
    old = State("person.conor", "home", {"in_zones": ["zone.home"]})
    new = State(
        "person.conor",
        "home",
        {"in_zones": ["zone.home", "zone.work"]},
    )

    assert not _zone_match(hass, "zone.work", old)
    assert _zone_match(hass, "zone.work", new)


def test_zone_match_supports_tracker_membership_without_coordinates() -> None:
    hass = FakeHass({"zone.work": _zone()})
    tracker = State("device_tracker.phone", "not_home", {"in_zones": ["zone.work"]})

    assert _zone_match(hass, "zone.work", tracker)


def test_zone_match_fails_closed_for_missing_zone_or_location_data() -> None:
    hass = FakeHass({"zone.work": _zone()})
    no_location = State("sensor.example", "on")

    assert not _zone_match(hass, "zone.missing", no_location)
    assert not _zone_match(hass, "zone.work", no_location)


def test_negated_state_condition_does_not_pass_for_missing_entity() -> None:
    passed, results = async_evaluate_conditions(
        FakeHass(),
        [
            {
                "type": "state",
                "entity_id": "binary_sensor.missing",
                "state": "on",
                "negate": True,
            }
        ],
        datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )

    assert not passed
    assert results == [{"type": "state", "passed": False, "actual": None}]


def test_negated_state_condition_does_not_pass_for_missing_attribute() -> None:
    hass = FakeHass({"sensor.mode": State("sensor.mode", "ok", {})})

    passed, results = async_evaluate_conditions(
        hass,
        [
            {
                "type": "state",
                "entity_id": "sensor.mode",
                "attribute": "mode",
                "state": "active",
                "negate": True,
            }
        ],
        datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )

    assert not passed
    assert results == [{"type": "state", "passed": False, "actual": None}]


def test_zone_condition_uses_in_zones_membership() -> None:
    hass = FakeHass(
        {
            "zone.work": _zone(),
            "person.conor": State(
                "person.conor",
                "home",
                {"in_zones": ["zone.home", "zone.work"]},
            ),
        }
    )

    passed, results = async_evaluate_conditions(
        hass,
        [
            {
                "type": "zone",
                "entity_id": "person.conor",
                "zone_entity_id": "zone.work",
            }
        ],
        datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )

    assert passed
    assert results[0]["passed"]


def test_zone_condition_fails_closed_for_missing_entities() -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    passed, results = async_evaluate_conditions(
        FakeHass({"zone.work": _zone()}),
        [
            {
                "type": "zone",
                "entity_id": "person.missing",
                "zone_entity_id": "zone.work",
            }
        ],
        now,
    )
    assert not passed
    assert not results[0]["passed"]

    passed, results = async_evaluate_conditions(
        FakeHass({"person.conor": State("person.conor", "home", {"in_zones": []})}),
        [
            {
                "type": "zone",
                "entity_id": "person.conor",
                "zone_entity_id": "zone.missing",
            }
        ],
        now,
    )
    assert not passed
    assert not results[0]["passed"]
