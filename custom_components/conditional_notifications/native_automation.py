"""Adapters for Home Assistant-native trigger and condition configurations."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import voluptuous as vol
from homeassistant.core import Context, Event, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import condition as condition_helper
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import trigger as trigger_helper
from homeassistant.helpers.condition import ConditionChecker
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .validation import DefinitionError

_LOGGER = logging.getLogger(__name__)


def is_native_trigger(definition: dict[str, Any]) -> bool:
    """Return whether a trigger uses Home Assistant's native trigger schema."""
    return (
        "trigger" in definition
        or "platform" in definition
        or ("triggers" in definition and "type" not in definition)
    )


def is_native_condition(definition: dict[str, Any]) -> bool:
    """Return whether a condition uses Home Assistant's native condition schema."""
    return "condition" in definition


def trigger_kind(definition: dict[str, Any] | None) -> str | None:
    """Return the semantic trigger kind for legacy or HA-native definitions."""
    if not definition:
        return None
    if is_native_trigger(definition):
        if "triggers" in definition and "trigger" not in definition and "platform" not in definition:
            return "group"
        value = definition.get("trigger", definition.get("platform"))
        return value if isinstance(value, str) else None
    value = definition.get("type")
    return value if isinstance(value, str) else None


def legacy_trigger_view(definition: dict[str, Any]) -> dict[str, Any] | None:
    """Project simple HA triggers into the legacy shape used by current-state logic.

    Native triggers remain fully native for subscription purposes; this conservative
    projection only enables lifecycle features whose meaning is well-defined for a
    single state, numeric-state, zone, or event trigger.
    """
    if not is_native_trigger(definition):
        return definition

    kind = trigger_kind(definition)
    if kind not in {"state", "numeric_state", "zone", "event"}:
        return None

    result: dict[str, Any] = {"type": kind}
    if kind in {"state", "numeric_state", "zone"}:
        entity_id = definition.get("entity_id")
        if not isinstance(entity_id, str):
            return None
        result["entity_id"] = entity_id

    if kind in {"state", "numeric_state"}:
        for key in ("attribute", "for"):
            if key in definition:
                result[key] = deepcopy(definition[key])

    if kind == "state":
        for key in ("from", "to"):
            if key not in definition:
                continue
            value = definition[key]
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                return None
            result[key] = value
    elif kind == "numeric_state":
        for key in ("above", "below"):
            if key not in definition:
                continue
            value = definition[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None
            result[key] = value
    elif kind == "zone":
        zone = definition.get("zone")
        if not isinstance(zone, str):
            return None
        result["zone_entity_id"] = zone
        result["event"] = definition.get("event")
    else:
        result["event_type"] = definition.get("event_type")
        if "event_data" in definition:
            result["event_data"] = deepcopy(definition["event_data"])

    return result


def _definition_error(path: str, err: Exception) -> DefinitionError:
    message = str(err).strip() or err.__class__.__name__
    return DefinitionError(path, message)


async def async_prepare_native_trigger(
    hass: HomeAssistant,
    definition: dict[str, Any],
    path: str,
) -> list[dict[str, Any]]:
    """Validate one stored native trigger/group using Home Assistant itself."""
    try:
        base = cv.TRIGGER_SCHEMA([deepcopy(definition)])
        validated = await trigger_helper.async_validate_trigger_config(hass, base)
    except (vol.Invalid, HomeAssistantError, KeyError, TypeError, ValueError) as err:
        raise _definition_error(path, err) from err
    if not validated:
        raise DefinitionError(path, "must contain at least one trigger")
    return validated


async def async_prepare_native_condition(
    hass: HomeAssistant,
    definition: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    """Validate one stored native condition using Home Assistant itself."""
    try:
        base = cv.CONDITIONS_SCHEMA([deepcopy(definition)])
        validated = await condition_helper.async_validate_conditions_config(hass, base)
    except (vol.Invalid, HomeAssistantError, KeyError, TypeError, ValueError) as err:
        raise _definition_error(path, err) from err
    if len(validated) != 1:
        raise DefinitionError(path, "must describe exactly one Home Assistant condition")
    return validated[0]


async def async_validate_native_definition(
    hass: HomeAssistant, definition: dict[str, Any]
) -> None:
    """Preflight all native automation fragments before durable mutation."""
    for index, trigger in enumerate(definition.get("triggers", [])):
        if is_native_trigger(trigger):
            await async_prepare_native_trigger(hass, trigger, f"triggers.{index}")

    for index, condition in enumerate(definition.get("conditions", [])):
        if is_native_condition(condition):
            await async_prepare_native_condition(hass, condition, f"conditions.{index}")

    resolve_when = definition.get("resolve_when")
    if isinstance(resolve_when, dict) and is_native_trigger(resolve_when):
        prepared = await async_prepare_native_trigger(hass, resolve_when, "resolve_when")
        if len(prepared) != 1:
            raise DefinitionError("resolve_when", "must describe exactly one Home Assistant trigger")


def _plain_value(value: Any, depth: int = 0) -> Any:
    """Convert HA runtime trigger payloads to durable JSON-like values."""
    if depth > 12:
        return "<maximum depth reached>"
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, State):
        return _plain_value(value.as_dict(), depth + 1)
    if isinstance(value, Event):
        return {
            "event_type": value.event_type,
            "data": _plain_value(value.data, depth + 1),
            "origin": str(value.origin),
            "time_fired": value.time_fired.isoformat(),
            "context": _plain_value(value.context, depth + 1),
        }
    if isinstance(value, Context):
        return {
            "id": value.id,
            "parent_id": value.parent_id,
            "user_id": value.user_id,
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return _plain_value(value.value, depth + 1)
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain_value(item, depth + 1) for item in value]
    return str(value)


def normalize_native_trigger_context(
    hass: HomeAssistant,
    run_variables: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """Build the bounded trigger snapshot used by lifecycle/history/templates."""
    raw_trigger = run_variables.get("trigger", {})
    trigger = _plain_value(raw_trigger)
    if not isinstance(trigger, dict):
        trigger = {"value": trigger}

    platform = trigger.get("platform") or trigger.get("trigger") or "home_assistant"
    trigger["type"] = str(platform)
    trigger["trigger_index"] = index
    trigger["timestamp"] = dt_util.now().isoformat()

    if "friendly_name" not in trigger:
        entity_id = trigger.get("entity_id")
        if isinstance(entity_id, str):
            state = hass.states.get(entity_id)
            trigger["friendly_name"] = (
                state.attributes.get("friendly_name", entity_id) if state else entity_id
            )
        else:
            trigger["friendly_name"] = str(platform).replace("_", " ").title()

    extra_variables = {
        key: _plain_value(value) for key, value in run_variables.items() if key != "trigger"
    }
    if extra_variables:
        trigger["variables"] = extra_variables
    return trigger


async def async_attach_native_trigger(
    runtime: Any,
    definition: dict[str, Any],
    index: int,
    accepted: Any,
    name: str,
) -> None:
    """Attach one native HA trigger/group to a Conditional Notifications slot."""
    hass: HomeAssistant = runtime.hass
    validated = await async_prepare_native_trigger(hass, definition, f"triggers.{index}")

    async def action(
        run_variables: dict[str, Any], context: Context | None = None
    ) -> None:
        del context
        accepted(normalize_native_trigger_context(hass, run_variables, index))

    def log_callback(level: int, message: str, **kwargs: Any) -> None:
        _LOGGER.log(level, "%s: %s", name, message, **kwargs)

    remove = await trigger_helper.async_initialize_triggers(
        hass,
        validated,
        action,
        DOMAIN,
        name,
        log_callback,
    )
    if remove is None:
        raise DefinitionError(f"triggers.{index}", "Home Assistant did not attach the trigger")
    runtime.add(remove)


async def async_attach_native_resolution_trigger(
    runtime: Any,
    definition: dict[str, Any],
    index: int,
    accepted: Any,
    name: str,
) -> None:
    """Attach the single native HA trigger used for auto-resolution."""
    hass: HomeAssistant = runtime.hass
    validated = await async_prepare_native_trigger(hass, definition, "resolve_when")
    if len(validated) != 1:
        raise DefinitionError("resolve_when", "must describe exactly one Home Assistant trigger")

    async def action(
        run_variables: dict[str, Any], context: Context | None = None
    ) -> None:
        del context
        accepted(normalize_native_trigger_context(hass, run_variables, index))

    def log_callback(level: int, message: str, **kwargs: Any) -> None:
        _LOGGER.log(level, "%s resolution: %s", name, message, **kwargs)

    remove = await trigger_helper.async_initialize_triggers(
        hass,
        validated,
        action,
        DOMAIN,
        f"{name} resolution",
        log_callback,
    )
    if remove is None:
        raise DefinitionError("resolve_when", "Home Assistant did not attach the resolution trigger")
    runtime.add(remove)


async def async_build_native_condition_checkers(
    hass: HomeAssistant,
    runtime: Any,
    conditions: list[dict[str, Any]],
) -> dict[int, ConditionChecker]:
    """Build and lifecycle-own native condition checkers by object identity."""
    checkers: dict[int, ConditionChecker] = {}
    try:
        for index, definition in enumerate(conditions):
            if not is_native_condition(definition):
                continue
            validated = await async_prepare_native_condition(
                hass, definition, f"conditions.{index}"
            )
            checker = await condition_helper.async_from_config(hass, validated)
            checkers[id(definition)] = checker
            runtime.add(checker.async_unload)
    except Exception:
        for checker in checkers.values():
            checker.async_unload()
        raise
    return checkers
