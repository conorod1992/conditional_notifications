"""Strict bounded schema validation and normalization."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import time
from typing import Any, Never
from urllib.parse import urlparse

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
        if "weekdays" in result:
            weekdays = result["weekdays"]
            if (
                not isinstance(weekdays, list)
                or not weekdays
                or any(day not in WEEKDAYS for day in weekdays)
            ):
                _error(f"{path}.weekdays", "must be a non-empty list of valid weekdays")
            result["weekdays"] = list(dict.fromkeys(weekdays))
    return result


def _companion_uri(value: Any, path: str) -> str:
    uri = str(value or "").strip()
    if not uri or len(uri) > 500:
        _error(path, "must be between 1 and 500 characters")
    if uri.startswith("/") and not uri.startswith("//"):
        return uri
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _error(path, "must be a Home Assistant path or an http/https URL")
    return uri


def _validate_companion(data: Any) -> dict[str, Any]:
    path = "delivery.companion"
    if not isinstance(data, dict):
        _error(path, "must be an object")
    result = deepcopy(data)
    if extra := set(result) - {"url", "actions"}:
        _error(path, f"contains unsupported fields: {', '.join(sorted(extra))}")
    if "url" in result:
        result["url"] = _companion_uri(result["url"], f"{path}.url")
    if "actions" in result:
        actions = result["actions"]
        if not isinstance(actions, list) or len(actions) > 3:
            _error(f"{path}.actions", "must be a list with at most 3 buttons")
        normalized: list[dict[str, str]] = []
        for index, action in enumerate(actions):
            item_path = f"{path}.actions.{index}"
            if not isinstance(action, dict):
                _error(item_path, "must be an object")
            if extra := set(action) - {"title", "action", "uri"}:
                _error(item_path, f"contains unsupported fields: {', '.join(sorted(extra))}")
            title = str(action.get("title", "")).strip()
            if not title or len(title) > 50:
                _error(f"{item_path}.title", "must be between 1 and 50 characters")
            has_action = bool(action.get("action"))
            has_uri = bool(action.get("uri"))
            if has_action == has_uri:
                _error(item_path, "must contain exactly one of action or uri")
            normalized_item = {"title": title}
            if has_uri:
                normalized_item["uri"] = _companion_uri(action["uri"], f"{item_path}.uri")
            else:
                action_id = str(action["action"]).strip()
                if not re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", action_id):
                    _error(
                        f"{item_path}.action",
                        "must use 1-64 letters, numbers, underscores, colons, or hyphens",
                    )
                if action_id in {"URI", "REPLY"}:
                    _error(f"{item_path}.action", "uses a reserved Companion App action ID")
                normalized_item["action"] = action_id
            normalized.append(normalized_item)
        result["actions"] = normalized
    if not result.get("url") and not result.get("actions"):
        _error(path, "needs a tap URL/path or at least one action button")
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
        "match_window",
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
    match = result.get("match", "any")
    if match not in {"any", "all_within"}:
        _error("match", "must be any or all_within")
    result["match"] = match
    if match == "all_within":
        if len(result.get("triggers", [])) < 2:
            _error("match", "all_within requires at least two triggers")
        try:
            match_window = duration_seconds(result.get("match_window"))
        except (TypeError, ValueError) as err:
            _error("match_window", str(err))
        if match_window <= 0:
            _error("match_window", "is required and must be greater than zero")
        if match_window > 86400:
            _error("match_window", "must be 24 hours or less")
        result["match_window"] = match_window
    else:
        result.pop("match_window", None)
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
    allowed_delivery = {
        "use_defaults",
        "persistent_notification",
        "notify_entities",
        "notify_services",
        "assist_satellites",
        "companion",
    }
    if set(delivery) - allowed_delivery:
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
    if "assist_satellites" in delivery and not isinstance(delivery["assist_satellites"], list):
        _error("delivery.assist_satellites", "must be a list")
    for entity_id in delivery.get("assist_satellites", []):
        if not isinstance(entity_id, str) or not entity_id.startswith("assist_satellite."):
            _error("delivery.assist_satellites", "may contain only Assist satellite entity IDs")
    if "companion" in delivery:
        delivery["companion"] = _validate_companion(delivery["companion"])
    result.setdefault("enabled", True)
    result.setdefault("match_current_state", False)
    return result
