"""Storage-safe validation for mixed legacy and Home Assistant-native definitions."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .native_automation import is_native_condition, is_native_trigger
from .validation import DefinitionError, validate_definition as _validate_legacy_definition

_MAX_NATIVE_DEPTH = 12
_MAX_NATIVE_NODES = 3000
_MAX_NATIVE_ITEMS = 500
_MAX_NATIVE_STRING = 20000


def _validate_native_value(value: Any, path: str, depth: int, counter: list[int]) -> None:
    """Bound arbitrary-looking HA config before async HA validation occurs."""
    counter[0] += 1
    if counter[0] > _MAX_NATIVE_NODES:
        raise DefinitionError(path, "is too large")
    if depth > _MAX_NATIVE_DEPTH:
        raise DefinitionError(path, "is nested too deeply")

    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > _MAX_NATIVE_STRING:
            raise DefinitionError(path, f"contains a string longer than {_MAX_NATIVE_STRING} characters")
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DefinitionError(path, "contains a non-finite number")
        return
    if isinstance(value, list):
        if len(value) > _MAX_NATIVE_ITEMS:
            raise DefinitionError(path, f"contains more than {_MAX_NATIVE_ITEMS} list items")
        for index, item in enumerate(value):
            _validate_native_value(item, f"{path}.{index}", depth + 1, counter)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_NATIVE_ITEMS:
            raise DefinitionError(path, f"contains more than {_MAX_NATIVE_ITEMS} fields")
        for key, item in value.items():
            if not isinstance(key, str):
                raise DefinitionError(path, "contains a non-string field name")
            if len(key) > 255:
                raise DefinitionError(path, "contains an excessively long field name")
            _validate_native_value(item, f"{path}.{key}", depth + 1, counter)
        return
    raise DefinitionError(path, f"contains unsupported value type {type(value).__name__}")


def _validate_native_fragment(
    value: Any,
    path: str,
    *,
    discriminator: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DefinitionError(path, "must be an object")
    result = deepcopy(value)
    _validate_native_value(result, path, 0, [0])

    if discriminator == "trigger":
        has_trigger = "trigger" in result
        has_platform = "platform" in result
        has_group = "triggers" in result and not has_trigger and not has_platform
        if has_group:
            group = result.get("triggers")
            if not isinstance(group, list) or not group:
                raise DefinitionError(f"{path}.triggers", "must be a non-empty trigger list")
            return result
        if has_trigger == has_platform:
            raise DefinitionError(path, "must contain exactly one of trigger or platform")
        key = "trigger" if has_trigger else "platform"
    else:
        key = "condition"
        if key not in result:
            raise DefinitionError(path, "must contain a condition type")

    kind = result.get(key)
    if not isinstance(kind, str) or not kind.strip():
        raise DefinitionError(f"{path}.{key}", "must be a non-empty string")
    return result


def validate_definition(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate a definition while preserving native HA automation fragments.

    The existing validator remains authoritative for Conditional Notifications'
    own lifecycle/storage fields. Native fragments are structurally bounded here
    and fully validated asynchronously by Home Assistant before persistence.
    """
    if not isinstance(data, dict):
        raise DefinitionError("definition", "must be an object")

    working = deepcopy(data)
    native_triggers: dict[int, dict[str, Any]] = {}
    native_conditions: dict[int, dict[str, Any]] = {}
    native_resolution: dict[str, Any] | None = None

    triggers = working.get("triggers")
    if isinstance(triggers, list):
        for index, item in enumerate(triggers):
            if isinstance(item, dict) and is_native_trigger(item):
                native_triggers[index] = _validate_native_fragment(
                    item, f"triggers.{index}", discriminator="trigger"
                )
                triggers[index] = {
                    "type": "state",
                    "entity_id": "binary_sensor.conditional_notifications_native_placeholder",
                    "to": "on",
                }

    conditions = working.get("conditions")
    if isinstance(conditions, list):
        for index, item in enumerate(conditions):
            if isinstance(item, dict) and is_native_condition(item):
                native_conditions[index] = _validate_native_fragment(
                    item, f"conditions.{index}", discriminator="condition"
                )
                conditions[index] = {
                    "type": "state",
                    "entity_id": "binary_sensor.conditional_notifications_native_placeholder",
                    "state": "on",
                }

    resolve_when = working.get("resolve_when")
    if isinstance(resolve_when, dict) and is_native_trigger(resolve_when):
        native_resolution = _validate_native_fragment(
            resolve_when, "resolve_when", discriminator="trigger"
        )
        working["resolve_when"] = {
            "type": "state",
            "entity_id": "binary_sensor.conditional_notifications_native_placeholder",
            "to": "off",
        }

    normalized = _validate_legacy_definition(working, partial=partial)

    if native_triggers:
        normalized_triggers = normalized.get("triggers")
        if not isinstance(normalized_triggers, list):
            raise DefinitionError("triggers", "must be a list")
        for index, value in native_triggers.items():
            normalized_triggers[index] = value

    if native_conditions:
        normalized_conditions = normalized.get("conditions")
        if not isinstance(normalized_conditions, list):
            raise DefinitionError("conditions", "must be a list")
        for index, value in native_conditions.items():
            normalized_conditions[index] = value

    if native_resolution is not None:
        normalized["resolve_when"] = native_resolution

    return normalized
