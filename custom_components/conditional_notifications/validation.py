"""Strict bounded schema validation and normalization."""

from __future__ import annotations

from copy import deepcopy
from datetime import time
from typing import Any, Never

from .const import WEEKDAYS
from .models import duration_seconds, parse_datetime


class DefinitionError(ValueError):
    """A user-correctable definition error."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _error(field: str, message: str) -> Never:
    raise DefinitionError(field, message)


def _duration(data: dict[str, Any], key: str) -> None:
    try:
        seconds = duration_seconds(data.get(key))
    except (TypeError, ValueError) as err:
        _error(key, str(err))
    if seconds:
        data[key] = seconds
    else:
        data.pop(key, None)


def _validate_trigger(
    trigger: dict[str, Any], path: str, *, resolve: bool = False
) -> dict[str, Any]:
    if not isinstance(trigger, dict):
        _error(path, "must be an object")
    result = deepcopy(trigger)
    kind = result.get("type")
    if kind not in {"state", "numeric_state", "zone", "event", "named"}:
        _error(f"{path}.type", "must be state, numeric_state, zone, event, or named")
    allowed_by_type = {
        "state": {"type", "entity_id", "from", "to", "attribute", "for"},
        "numeric_state": {"type", "entity_id", "above", "below", "attribute", "for"},
        "zone": {"type", "entity_id", "zone_entity_id", "event"},
        "event": {"type", "event_type", "event_data"},
        "named": {"type", "trigger_id"},
    }
    if extra := set(result) - allowed_by_type[kind]:
        _error(path, f"contains unsupported fields: {', '.join(sorted(extra))}")
    if kind in {"state", "numeric_state", "zone"} and not result.get("entity_id"):
        _error(f"{path}.entity_id", "is required")
    if kind == "state":
        if "from" not in result and "to" not in result:
            _error(path, "a state trigger needs from or to")
        if result.get("from") == result.get("to") and "from" in result and "to" in result:
            _error(path, "from and to must differ")
    elif kind == "numeric_state":
        if "above" not in result and "below" not in result:
            _error(path, "a numeric trigger needs above or below")
        try:
            if "above" in result:
                result["above"] = float(result["above"])
            if "below" in result:
                result["below"] = float(result["below"])
        except (TypeError, ValueError):
            _error(path, "numeric thresholds must be numbers")
        if "above" in result and "below" in result and result["above"] >= result["below"]:
            _error(path, "above must be less than below")
    elif kind == "zone":
        if not result.get("zone_entity_id", "").startswith("zone."):
            _error(f"{path}.zone_entity_id", "must be a zone entity")
        if result.get("event") not in {"enter", "leave"}:
            _error(f"{path}.event", "must be enter or leave")
    elif kind == "event":
        if not result.get("event_type"):
            _error(f"{path}.event_type", "is required")
        if not isinstance(result.get("event_data", {}), dict):
            _error(f"{path}.event_data", "must be an object")
    elif kind == "named" and not result.get("trigger_id"):
        _error(f"{path}.trigger_id", "is required")
    _duration(result, "for")
    if resolve and result.get("for"):
        _error(f"{path}.for", "resolution duration is not supported")
    return result


def _validate_condition(condition: dict[str, Any], path: str) -> dict[str, Any]:
    if not isinstance(condition, dict):
        _error(path, "must be an object")
    result = deepcopy(condition)
    kind = result.get("type")
    if kind not in {"state", "numeric_state", "zone", "time"}:
        _error(f"{path}.type", "must be state, numeric_state, zone, or time")
    allowed_by_type = {
        "state": {"type", "entity_id", "state", "attribute", "negate"},
        "numeric_state": {"type", "entity_id", "above", "below", "attribute"},
        "zone": {"type", "entity_id", "zone_entity_id"},
        "time": {"type", "after", "before", "weekdays"},
    }
    if extra := set(result) - allowed_by_type[kind]:
        _error(path, f"contains unsupported fields: {', '.join(sorted(extra))}")
    if kind in {"state", "numeric_state", "zone"} and not result.get("entity_id"):
        _error(f"{path}.entity_id", "is required")
    if kind == "state" and "state" not in result:
        _error(f"{path}.state", "is required")
    if kind == "numeric_state":
        return _validate_trigger({**result, "type": "numeric_state"}, path)
    if kind == "zone" and not str(result.get("zone_entity_id", "")).startswith("zone."):
        _error(f"{path}.zone_entity_id", "must be a zone entity")
    if kind == "time":
        try:
            if "after" in result:
                time.fromisoformat(result["after"])
            if "before" in result:
                time.fromisoformat(result["before"])
        except (TypeError, ValueError):
            _error(path, "time values must use HH:MM or HH:MM:SS")
        if "after" not in result and "before" not in result:
            _error(path, "a time condition needs after or before")
    return result


def validate_definition(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate and normalize a complete record definition."""
    if not isinstance(data, dict):
        _error("definition", "must be an object")
    result = deepcopy(data)
    allowed = {
        "name",
        "semantic_key",
        "description",
        "triggers",
        "match",
        "conditions",
        "title",
        "message",
        "delivery",
        "available_from",
        "expires_at",
        "active_window",
        "repeat_policy",
        "max_notifications",
        "cooldown",
        "debounce",
        "notify_on_expiry",
        "expiry_title",
        "expiry_message",
        "resolve_when",
        "resolved_title",
        "resolved_message",
        "clear_on_resolve",
        "match_current_state",
        "enabled",
    }
    if extra := set(result) - allowed:
        _error("definition", f"contains unsupported fields: {', '.join(sorted(extra))}")
    if not partial or "name" in result:
        name = str(result.get("name", "")).strip()
        if not name:
            _error("name", "is required")
        if len(name) > 100:
            _error("name", "must be 100 characters or fewer")
        result["name"] = name
    if not partial or "triggers" in result:
        triggers = result.get("triggers")
        if not isinstance(triggers, list) or not triggers:
            _error("triggers", "at least one trigger is required")
        if len(triggers) > 20:
            _error("triggers", "at most 20 triggers are allowed")
        result["triggers"] = [
            _validate_trigger(item, f"triggers.{index}") for index, item in enumerate(triggers)
        ]
    if result.get("match", "any") != "any":
        _error("match", "v1 supports only 'any'")
    result["match"] = "any"
    if "conditions" in result:
        if not isinstance(result["conditions"], list) or len(result["conditions"]) > 20:
            _error("conditions", "must be a list with at most 20 items")
        result["conditions"] = [
            _validate_condition(item, f"conditions.{index}")
            for index, item in enumerate(result["conditions"])
        ]
    policy = result.get("repeat_policy", "once")
    if policy not in {"once", "every", "limited"}:
        _error("repeat_policy", "must be once, every, or limited")
    result["repeat_policy"] = policy
    if policy == "limited":
        try:
            result["max_notifications"] = int(result.get("max_notifications", 0))
        except (TypeError, ValueError):
            _error("max_notifications", "must be a positive integer")
        if result["max_notifications"] < 1 or result["max_notifications"] > 10000:
            _error("max_notifications", "must be between 1 and 10000")
    elif "max_notifications" in result:
        result.pop("max_notifications")
    for key in ("cooldown", "debounce"):
        _duration(result, key)
    for key in ("available_from", "expires_at"):
        if result.get(key):
            try:
                parsed = parse_datetime(result[key])
                assert parsed is not None
                result[key] = parsed.isoformat()
            except (TypeError, ValueError) as err:
                _error(key, str(err))
    available = parse_datetime(result.get("available_from"))
    expires = parse_datetime(result.get("expires_at"))
    if available and expires and expires <= available:
        _error("expires_at", "must be after available_from")
    if result.get("active_window"):
        window = result["active_window"]
        if not isinstance(window, dict):
            _error("active_window", "must be an object")
        try:
            time.fromisoformat(window["start"])
            time.fromisoformat(window["end"])
        except (KeyError, TypeError, ValueError):
            _error("active_window", "start and end must be valid local times")
        weekdays = window.get("weekdays", list(WEEKDAYS))
        if not weekdays or any(day not in WEEKDAYS for day in weekdays):
            _error("active_window.weekdays", "contains an invalid weekday")
        window["weekdays"] = list(dict.fromkeys(weekdays))
    if result.get("resolve_when"):
        result["resolve_when"] = _validate_trigger(
            result["resolve_when"], "resolve_when", resolve=True
        )
    for key in ("title", "message"):
        if not partial and not str(result.get(key, "")).strip():
            _error(key, "is required")
        if key in result and len(str(result[key])) > (255 if key == "title" else 4000):
            _error(key, "is too long")
    result.setdefault("notify_on_expiry", False)
    if result["notify_on_expiry"] and not result.get("expires_at"):
        _error("notify_on_expiry", "requires expires_at")
    delivery = result.setdefault("delivery", {"use_defaults": True})
    if not isinstance(delivery, dict):
        _error("delivery", "must be an object")
    allowed = {"use_defaults", "persistent_notification", "notify_entities", "notify_services"}
    if set(delivery) - allowed:
        _error("delivery", "contains unsupported delivery fields")
    if "notify_services" in delivery and not isinstance(delivery["notify_services"], list):
        _error("delivery.notify_services", "must be a list")
    for service in delivery.get("notify_services", []):
        if not isinstance(service, str) or ("." in service and not service.startswith("notify.")):
            _error("delivery.notify_services", "may contain only notify service names")
    if "notify_entities" in delivery and not isinstance(delivery["notify_entities"], list):
        _error("delivery.notify_entities", "must be a list")
    for entity_id in delivery.get("notify_entities", []):
        if not isinstance(entity_id, str) or not entity_id.startswith("notify."):
            _error("delivery.notify_entities", "may contain only notify entity IDs")
    result.setdefault("enabled", True)
    result.setdefault("match_current_state", False)
    return result
