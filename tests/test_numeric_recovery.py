"""Numeric-state recovery regression tests."""

from custom_components.conditional_notifications.triggers import _numeric_match
from homeassistant.core import State


def state(value: str) -> State:
    return State("sensor.example", value)


def test_numeric_recovery_into_matching_range_triggers() -> None:
    definition = {
        "type": "numeric_state",
        "entity_id": "sensor.example",
        "above": 10,
        "below": 20,
    }

    matched, previous, current = _numeric_match(
        definition,
        state("unavailable"),
        state("11"),
    )

    assert matched
    assert previous is None
    assert current == 11
    assert _numeric_match(definition, state("unknown"), state("11"))[0]
    assert _numeric_match(definition, None, state("11"))[0]
    assert not _numeric_match(definition, state("unavailable"), state("25"))[0]
