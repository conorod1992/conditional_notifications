"""Strict bounded schema validation and normalization."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import time
from typing import Any, Never
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

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


def _entity_id(value: Any, path: str, *, domain: str | None = None) -> str:
    try:
        entity_id = cv.entity_id(value)
    except vol.Invalid:
        _error(path, "must be a valid entity ID")
    if domain is not None and entity_id.split(".", 1)[0] != domain:
        _error(path, f"must be a {domain} entity")
    return entity_id


def _validate_attribute(data: dict[str, Any], path: str) -> None:
    if "attribute" not in data:
        return
    if not isinstance(data["attribute"], str) or not data["attribute"]:
        _error(path, "must be a non-empty attribute name")


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool):
        _error(path, "must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        _error(path, "must be a finite number")
    if not math.isfinite(number):
        _error(path, "must be a finite number")
    return number


def _strict_bool(data: dict[str, Any], key: str, path: str, *, default: bool | None = None) -> None:
    if key in data:
        if not isinstance(data[key], bool):
            _error(path, "must be true or false")
    elif default is not None:
        data[key] = default


def _bounded_string(value: Any, path: str, label: str, *, maximum: int = 255) -> str:
    if not isinstance(value, str):
        _error(path, f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        _error(path, "is required")
    if len(normalized) > maximum:
        _error(path, f"must be {maximum} characters or fewer")
    return normalized


def _local_time(value: Any, path: str) -> time:
    if not isinstance(value, str):
        _error(path, "must use HH:MM or HH:MM:SS")
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        _error(path, "must use HH:MM or HH:MM:SS")
    if parsed.tzinfo is not None:
        _error(path, "must be a local time without a timezone offset")
    return parsed


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
    if kind in {"state", "numeric_state", "zone"}:
        result["entity_id"] = _entity_id(result.get("entity_id"), f"{path}.entity_id")
    if kind in {"state", "numeric_state"}:
        _validate_attribute(result, f"{path}.attribute")
    if kind == "state":
        if "from" not in result and "to" not in result:
            _error(path, "a state trigger needs from or to")
        if result.get("from") == result.get("to") and "from" in result and "to" in result:
            _error(path, "from and to must differ")
        if "attribute" not in result:
            for key in ("from", "to"):
                if key in result and not isinstance(result[key], str):
                    _error(
                        f"{path}.{key}",
                        "must be a string when no attribute is selected",
                    )
    elif kind == "numeric_state":
        if "above" not in result and "below" not in result:
            _error(path, "a numeric trigger needs above or below")
        if "above" in result:
            result["above"] = _finite_number(result["above"], f"{path}.above")
        if "below" in result:
            result["below"] = _finite_number(result["below"], f"{path}.below")
        if "above" in result and "below" in result and result["above"] >= result["below"]:
            _error(path, "above must be less than below")
    elif kind == "zone":
        result["zone_entity_id"] = _entity_id(
            result.get("zone_entity_id"), f"{path}.zone_entity_id", domain="zone"
        )
        if result.get("event") not in {"enter", "leave"}:
            _error(f"{path}.event", "must be enter or leave")
    elif kind == "event":
        event_type = _bounded_string(result.get("event_type"), f"{path}.event_type", "event type")
        if event_type in {"*", "state_reported"}:
            _error(
                f"{path}.event_type",
                "uses a Home Assistant reserved event type that cannot be watched here",
            )
        result["event_type"] = event_type
        if not isinstance(result.get("event_data", {}), dict):
            _error(f"{path}.event_data", "must be an object")
    elif kind == "named":
        result["trigger_id"] = _bounded_string(
            result.get("trigger_id"), f"{path}.trigger_id", "trigger name"
        )
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
    if kind in {"state", "numeric_state", "zone"}:
        result["entity_id"] = _entity_id(result.get("entity_id"), f"{path}.entity_id")
    if kind in {"state", "numeric_state"}:
        _validate_attribute(result, f"{path}.attribute")
    if kind == "state":
        if "state" not in result:
            _error(f"{path}.state", "is required")
        if "attribute" not in result and not isinstance(result["state"], str):
            _error(
                f"{path}.state",
                "must be a string when no attribute is selected",
            )
        _strict_bool(result, "negate", f"{path}.negate")
    if kind == "numeric_state":
        return _validate_trigger({**result, "type": "numeric_state"}, path)
    if kind == "zone":
        result["zone_entity_id"] = _entity_id(
            result.get("zone_entity_id"), f"{path}.zone_entity_id", domain="zone"
        )
    if kind == "time":
        if "after" in result:
            _local_time(result["after"], f"{path}.after")
        if "before" in result:
            _local_time(result["before"], f"{path}.before")
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
    if not isinstance(value, str):
        _error(path, "must be a string")
    uri = value.strip()
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
            title = action.get("title")
            if not isinstance(title, str):
                _error(f"{item_path}.title", "must be a string")
            title = title.strip()
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
                action_id = action["action"]
                if not isinstance(action_id, str):
                    _error(f"{item_path}.action", "must be a string")
                action_id = action_id.strip()
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
        name = result.get("name")
        if not isinstance(name, str):
            _error("name", "must be a string")
        name = name.strip()
        if not name:
            _error("name", "is required")
        if len(name) > 100:
            _error("name", "must be 100 characters or fewer")
        result["name"] = name
    for key in ("semantic_key", "description"):
        if key in result and result[key] is not None and not isinstance(result[key], str):
            _error(key, "must be a string or null")
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
        if isinstance(result.get("max_notifications"), bool):
            _error("max_notifications", "must be a positive integer")
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
        if key not in result:
            continue
        if result[key] is None or result[key] == "":
            result.pop(key)
            continue
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
    if "active_window" in result:
        window = result["active_window"]
        if window is None or window == {}:
            result.pop("active_window")
        else:
            if not isinstance(window, dict):
                _error("active_window", "must be an object")
            if extra := set(window) - {"start", "end", "weekdays"}:
                _error(
                    "active_window",
                    f"contains unsupported fields: {', '.join(sorted(extra))}",
                )
            try:
                _local_time(window["start"], "active_window.start")
                _local_time(window["end"], "active_window.end")
            except KeyError:
                _error("active_window", "start and end must be valid local times")
            weekdays = window.get("weekdays", list(WEEKDAYS))
            if (
                not isinstance(weekdays, list)
                or not weekdays
                or any(day not in WEEKDAYS for day in weekdays)
            ):
                _error(
                    "active_window.weekdays",
                    "must be a non-empty list of valid weekdays",
                )
            window["weekdays"] = list(dict.fromkeys(weekdays))
    if "resolve_when" in result:
        resolve_when = result["resolve_when"]
        if resolve_when is None or resolve_when == {}:
            result.pop("resolve_when")
        elif not isinstance(resolve_when, dict):
            _error("resolve_when", "must be an object")
        else:
            result["resolve_when"] = _validate_trigger(
                resolve_when, "resolve_when", resolve=True
            )
    text_limits = {
        "title": 255,
        "message": 4000,
        "expiry_title": 255,
        "expiry_message": 4000,
        "resolved_title": 255,
        "resolved_message": 4000,
    }
    for key, maximum in text_limits.items():
        if key in result:
            if not isinstance(result[key], str):
                _error(key, "must be a string")
            if len(result[key]) > maximum:
                _error(key, "is too long")
    for key in ("title", "message"):
        if not partial and not result.get(key, "").strip():
            _error(key, "is required")
    for key, default in (
        ("notify_on_expiry", False),
        ("clear_on_resolve", True),
        ("match_current_state", False),
        ("enabled", True),
    ):
        _strict_bool(result, key, key, default=default)
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
    _strict_bool(delivery, "use_defaults", "delivery.use_defaults")
    _strict_bool(delivery, "persistent_notification", "delivery.persistent_notification")
    if "notify_services" in delivery and not isinstance(delivery["notify_services"], list):
        _error("delivery.notify_services", "must be a list")
    for service in delivery.get("notify_services", []):
        if not isinstance(service, str) or ("." in service and not service.startswith("notify.")):
            _error("delivery.notify_services", "may contain only notify service names")
    if "notify_entities" in delivery and not isinstance(delivery["notify_entities"], list):
        _error("delivery.notify_entities", "must be a list")
    if "notify_entities" in delivery:
        for entity_id in delivery["notify_entities"]:
            if not isinstance(entity_id, str) or not entity_id.startswith("notify."):
                _error("delivery.notify_entities", "may contain only notify entity IDs")
        delivery["notify_entities"] = [
            _entity_id(entity_id, "delivery.notify_entities", domain="notify")
            for entity_id in delivery["notify_entities"]
        ]
    if "assist_satellites" in delivery and not isinstance(delivery["assist_satellites"], list):
        _error("delivery.assist_satellites", "must be a list")
    if "assist_satellites" in delivery:
        for entity_id in delivery["assist_satellites"]:
            if not isinstance(entity_id, str) or not entity_id.startswith("assist_satellite."):
                _error(
                    "delivery.assist_satellites",
                    "may contain only Assist satellite entity IDs",
                )
        delivery["assist_satellites"] = [
            _entity_id(
                entity_id,
                "delivery.assist_satellites",
                domain="assist_satellite",
            )
            for entity_id in delivery["assist_satellites"]
        ]
    if "companion" in delivery:
        delivery["companion"] = _validate_companion(delivery["companion"])
    return result