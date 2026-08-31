"""Bounded condition evaluation."""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import Any

from homeassistant.components.zone.condition import zone as zone_condition
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConditionError, HomeAssistantError, TemplateError

from .const import UNKNOWN_STATES, WEEKDAYS
from .native_context import CURRENT_CONDITION_CHECKERS, CURRENT_TRIGGER


def is_unknown_state(value: Any) -> bool:
    """Return whether a scalar state value is HA unknown/unavailable."""
    return isinstance(value, str) and value in UNKNOWN_STATES


def _numeric_value(state: Any, attribute: str | None) -> float | None:
    if state is None:
        return None
    raw = state.attributes.get(attribute) if attribute else state.state
    if raw is None or is_unknown_state(raw):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def numeric_matches(value: float | None, definition: dict[str, Any]) -> bool:
    """Return whether a number is inside the strict configured bounds."""
    return (
        value is not None
        and ("above" not in definition or value > float(definition["above"]))
        and ("below" not in definition or value < float(definition["below"]))
    )


def _evaluate_native_condition(
    definition: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Evaluate a pre-built HA condition checker, failing closed on errors."""
    kind = definition.get("condition", "home_assistant")
    checkers = CURRENT_CONDITION_CHECKERS.get() or {}
    checker = checkers.get(id(definition))
    if checker is None:
        return False, {
            "type": kind,
            "native": True,
            "passed": False,
            "error": "condition checker is unavailable",
        }

    trigger = CURRENT_TRIGGER.get() or {}
    try:
        result = checker.async_check(variables={"trigger": trigger})
        passed = result is not False
        return passed, {"type": kind, "native": True, "passed": passed}
    except (ConditionError, HomeAssistantError, TemplateError, TypeError, ValueError) as err:
        return False, {
            "type": kind,
            "native": True,
            "passed": False,
            "error": str(err)[:300],
        }


def async_evaluate_conditions(
    hass: HomeAssistant, conditions: list[dict[str, Any]], now: datetime
) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluate all conditions using AND semantics."""
    results: list[dict[str, Any]] = []
    for definition in conditions:
        if "condition" in definition:
            passed, detail = _evaluate_native_condition(definition)
            results.append(detail)
            if not passed:
                return False, results
            continue

        kind = definition["type"]
        passed = False
        actual: Any = None
        if kind == "state":
            state = hass.states.get(definition["entity_id"])
            actual = (
                state.attributes.get(definition["attribute"])
                if state and definition.get("attribute")
                else (state.state if state else None)
            )
            expected = definition["state"]
            known = actual is not None and not is_unknown_state(actual)
            passed = known and (
                actual != expected if definition.get("negate") else actual == expected
            )
        elif kind == "numeric_state":
            state = hass.states.get(definition["entity_id"])
            actual = _numeric_value(state, definition.get("attribute"))
            passed = numeric_matches(actual, definition)
        elif kind == "zone":
            try:
                passed = zone_condition(hass, definition["zone_entity_id"], definition["entity_id"])
            except (ConditionError, AttributeError, ValueError):
                passed = False
        elif kind == "time":
            local = now.timetz().replace(tzinfo=None)
            after = time.fromisoformat(definition["after"]) if definition.get("after") else None
            before = time.fromisoformat(definition["before"]) if definition.get("before") else None
            passed = (after is None or local >= after) and (before is None or local < before)
            overnight = bool(after and before and after > before)
            if overnight:
                assert after is not None and before is not None
                passed = local >= after or local < before
            weekdays = definition.get("weekdays")
            weekday_index = now.weekday()
            if overnight and before and local < before:
                weekday_index = (weekday_index - 1) % len(WEEKDAYS)
            passed = passed and (not weekdays or WEEKDAYS[weekday_index] in weekdays)
            actual = local.isoformat()
        results.append({"type": kind, "passed": passed, "actual": actual})
        if not passed:
            return False, results
    return True, results


def state_value(state: Any, attribute: str | None) -> Any:
    """Extract a state or attribute safely."""
    if state is None:
        return None
    return state.attributes.get(attribute) if attribute else state.state
