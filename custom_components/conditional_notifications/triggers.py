"""Direct state/event subscriptions and exact duration callbacks."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.zone.condition import zone as zone_condition
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import ConditionError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .conditions import is_unknown_state, numeric_matches, state_value
from .const import DOMAIN


def _subset(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        if isinstance(value, dict):
            if not isinstance(actual[key], dict) or not _subset(value, actual[key]):
                return False
        elif actual[key] != value:
            return False
    return True


def _friendly(state: State | None, entity_id: str) -> str:
    return str(state.attributes.get("friendly_name", entity_id)) if state else entity_id


def _state_match(definition: dict[str, Any], old: State | None, new: State | None) -> bool:
    if old is None:
        return False
    old_value = state_value(old, definition.get("attribute"))
    new_value = state_value(new, definition.get("attribute"))
    if new_value is None or is_unknown_state(new_value) or old_value == new_value:
        return False
    return ("from" not in definition or old_value == definition["from"]) and (
        "to" not in definition or new_value == definition["to"]
    )


def _state_still_matches(definition: dict[str, Any], state: State | None) -> bool:
    """Return whether a pending state duration remains continuously valid."""
    value = state_value(state, definition.get("attribute"))
    if value is None or is_unknown_state(value):
        return False
    if "to" in definition:
        return value == definition["to"]
    return "from" in definition and value != definition["from"]


def _numeric_match(
    definition: dict[str, Any], old: State | None, new: State | None
) -> tuple[bool, float | None, float | None]:
    def number(state: State | None) -> float | None:
        raw = state_value(state, definition.get("attribute"))
        if raw is None or is_unknown_state(raw):
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    previous, current = number(old), number(new)
    return (
        previous is not None
        and numeric_matches(current, definition)
        and not numeric_matches(previous, definition),
        previous,
        current,
    )


def _zone_match(hass: HomeAssistant, zone_entity_id: str, state: State | None) -> bool:
    """Match a state against a zone using Home Assistant's canonical semantics."""
    if state is None or (zone_state := hass.states.get(zone_entity_id)) is None:
        return False
    try:
        return zone_condition(hass, zone_state, state)
    except ConditionError:
        return False


class RuntimeSubscriptions:
    """All cancellable callbacks for one revision of a record."""

    def __init__(self, hass: HomeAssistant, notification_id: str, revision: int) -> None:
        self.hass = hass
        self.notification_id = notification_id
        self.revision = revision
        self._unsubscribers: list[Callable[[], None]] = []
        self._duration: dict[int, Callable[[], None]] = {}

    def add(self, unsubscribe: Callable[[], None]) -> None:
        self._unsubscribers.append(unsubscribe)

    def cancel(self) -> None:
        for unsubscribe in self._duration.values():
            unsubscribe()
        self._duration.clear()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def cancel_duration(self, index: int) -> None:
        if unsubscribe := self._duration.pop(index, None):
            unsubscribe()

    def schedule_duration(self, index: int, seconds: float, action: Callable[[], None]) -> None:
        self.cancel_duration(index)

        @callback
        def finished(_: datetime) -> None:
            self._duration.pop(index, None)
            action()

        self._duration[index] = async_call_later(self.hass, seconds, finished)


def attach_trigger(
    runtime: RuntimeSubscriptions,
    definition: dict[str, Any],
    index: int,
    accepted: Callable[[dict[str, Any]], None],
) -> None:
    """Attach a bounded trigger definition to HA's event bus."""
    hass = runtime.hass
    kind = definition["type"]

    def dispatch(context: dict[str, Any]) -> None:
        context["trigger_index"] = index
        context["type"] = kind
        context["timestamp"] = dt_util.now().isoformat()
        accepted(context)

    if kind in {"state", "numeric_state", "zone"}:
        entity_id = definition["entity_id"]

        @callback
        def state_changed(event: Event) -> None:
            old: State | None = event.data.get("old_state")
            new: State | None = event.data.get("new_state")
            if new is None:
                runtime.cancel_duration(index)
                return
            context: dict[str, Any]
            matches = False
            if kind == "state":
                matches = _state_match(definition, old, new)
                context = {
                    "entity_id": entity_id,
                    "friendly_name": _friendly(new, entity_id),
                    "from_state": state_value(old, definition.get("attribute")),
                    "to_state": state_value(new, definition.get("attribute")),
                    "attribute": definition.get("attribute"),
                }
            elif kind == "numeric_state":
                matches, previous, current = _numeric_match(definition, old, new)
                context = {
                    "entity_id": entity_id,
                    "friendly_name": _friendly(new, entity_id),
                    "previous_value": previous,
                    "value": current,
                    "above": definition.get("above"),
                    "below": definition.get("below"),
                    "attribute": definition.get("attribute"),
                }
            else:
                zone_entity_id = definition["zone_entity_id"]
                zone_state = hass.states.get(zone_entity_id)
                zone_name = (
                    zone_state.attributes.get("friendly_name")
                    if zone_state
                    else zone_entity_id.split(".", 1)[-1].replace("_", " ")
                )
                old_inside = _zone_match(hass, zone_entity_id, old)
                new_inside = _zone_match(hass, zone_entity_id, new)
                matches = (definition["event"] == "enter" and not old_inside and new_inside) or (
                    definition["event"] == "leave" and old_inside and not new_inside
                )
                context = {
                    "entity_id": entity_id,
                    "friendly_name": _friendly(new, entity_id),
                    "zone_entity_id": zone_entity_id,
                    "zone": zone_name,
                    "event": definition["event"],
                }
            if not matches:
                # Unrelated attribute updates and subsequent updates while still
                # inside a numeric range must not cancel an in-flight duration.
                still_matching = False
                if kind == "state":
                    still_matching = _state_still_matches(definition, new)
                elif kind == "numeric_state":
                    try:
                        value = float(state_value(new, definition.get("attribute")))
                    except (TypeError, ValueError):
                        value = None
                    if value is not None and not math.isfinite(value):
                        value = None
                    still_matching = numeric_matches(value, definition)
                if not still_matching:
                    runtime.cancel_duration(index)
                return
            seconds = float(definition.get("for", 0))
            if not seconds:
                dispatch(context)
                return

            def duration_done() -> None:
                current = hass.states.get(entity_id)
                still_matches = False
                if kind == "state":
                    still_matches = _state_still_matches(definition, current)
                elif kind == "numeric_state":
                    try:
                        value = float(state_value(current, definition.get("attribute")))
                    except (TypeError, ValueError):
                        value = None
                    if value is not None and not math.isfinite(value):
                        value = None
                    still_matches = numeric_matches(value, definition)
                if still_matches:
                    dispatch(context)

            runtime.schedule_duration(index, seconds, duration_done)

        runtime.add(async_track_state_change_event(hass, [entity_id], state_changed))
        return

    event_type = definition.get("event_type") if kind == "event" else f"{DOMAIN}_named_trigger"

    @callback
    def event_received(event: Event) -> None:
        if kind == "event":
            if not _subset(definition.get("event_data", {}), event.data):
                return
            dispatch({"event_type": event.event_type, "event_data": dict(event.data)})
        elif event.data.get("trigger_id") == definition["trigger_id"]:
            dispatch({"trigger_id": definition["trigger_id"], "event_data": dict(event.data)})

    runtime.add(hass.bus.async_listen(event_type, event_received))


def schedule_task(hass: HomeAssistant, coroutine: Any) -> None:
    """Create a tracked background task from a synchronous event callback."""
    hass.async_create_task(coroutine, eager_start=True)
