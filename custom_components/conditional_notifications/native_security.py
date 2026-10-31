"""Authorization checks for Home Assistant-native trigger and condition fragments."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.auth.permissions.events import SUBSCRIBE_ALLOWLIST
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .native_automation import is_native_condition, is_native_trigger, trigger_kind
from .validation import DefinitionError

_SAFE_TRIGGER_KINDS = {
    "state",
    "numeric_state",
    "zone",
    "event",
    "time",
    "time_pattern",
    "sun",
    "homeassistant",
    "calendar",
}
_SAFE_CONDITION_KINDS = {
    "state",
    "numeric_state",
    "zone",
    "time",
    "sun",
    "trigger",
    "and",
    "or",
    "not",
}
_TEMPLATE_MARKERS = ("{{", "{%", "{#")


def _as_entity_ids(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item


def _looks_like_entity_id(value: str) -> bool:
    return "." in value and not value.startswith(".") and " " not in value


def _require_entity_read(
    permissions: Any,
    unrestricted: bool,
    value: Any,
    path: str,
) -> None:
    if unrestricted:
        return
    for entity_id in _as_entity_ids(value):
        if not _looks_like_entity_id(entity_id):
            continue
        if not permissions.check_entity(entity_id, POLICY_READ):
            raise DefinitionError(path, "is not readable by the notification owner")


def _require_non_templated(value: Any, path: str) -> None:
    """Reject observer templates for non-admin owners.

    Entity permissions cannot reliably constrain every object a Home Assistant
    template can inspect, so native observer templates remain administrator-only.
    """
    if isinstance(value, str) and any(marker in value for marker in _TEMPLATE_MARKERS):
        raise DefinitionError(path, "templated observers require administrator access")
    if isinstance(value, dict):
        for key, child in value.items():
            _require_non_templated(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _require_non_templated(child, f"{path}.{index}")


def _resolve_observer_entities(hass: HomeAssistant, value: Any, path: str) -> Any:
    """Authorize the same entity IDs HA will observe, including registry UUIDs."""
    if isinstance(value, list):
        return [
            _resolve_observer_entities(hass, child, f"{path}.{index}")
            for index, child in enumerate(value)
        ]
    if not isinstance(value, dict):
        return value
    result = dict(value)
    for key, child in value.items():
        child_path = f"{path}.{key}"
        if key in {"event_data", "event_context", "variables"}:
            # Literal filters/variables are data, not entity observers. Template
            # strings in these structures were already checked recursively.
            continue
        if key in {"entity_id", "zone"}:
            ids = [child] if isinstance(child, str) else child
            if not isinstance(ids, list):
                raise DefinitionError(child_path, "must reference an entity")
            resolved = []
            for entity_id in ids:
                if not isinstance(entity_id, str):
                    raise DefinitionError(child_path, "must reference an entity")
                if not _looks_like_entity_id(entity_id):
                    try:
                        entity_id = er.async_validate_entity_id(er.async_get(hass), entity_id)
                    except vol.Invalid as err:
                        raise DefinitionError(child_path, "does not resolve to an entity") from err
                resolved.append(entity_id)
            result[key] = resolved[0] if isinstance(child, str) else resolved
        else:
            result[key] = _resolve_observer_entities(hass, child, child_path)
    return result


def _check_time_trigger_value(
    value: Any,
    path: str,
    permissions: Any,
    unrestricted: bool,
) -> None:
    """Authorize each literal/entity entry accepted by HA's time trigger."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_time_trigger_value(
                item,
                f"{path}.{index}",
                permissions,
                unrestricted,
            )
        return
    if isinstance(value, dict):
        _require_entity_read(
            permissions,
            unrestricted,
            value.get("entity_id"),
            f"{path}.entity_id",
        )
        return
    if isinstance(value, str):
        _require_non_templated(value, path)
        if _looks_like_entity_id(value):
            _require_entity_read(permissions, unrestricted, value, path)


def _check_native_trigger(
    definition: dict[str, Any],
    path: str,
    permissions: Any,
    unrestricted: bool,
) -> None:
    kind = trigger_kind(definition)
    if kind == "group":
        children = definition.get("triggers")
        if not isinstance(children, list) or not children:
            raise DefinitionError(f"{path}.triggers", "must be a non-empty trigger list")
        for index, child in enumerate(children):
            if not isinstance(child, dict) or not is_native_trigger(child):
                raise DefinitionError(
                    f"{path}.triggers.{index}", "must be a Home Assistant trigger"
                )
            _check_native_trigger(
                child,
                f"{path}.triggers.{index}",
                permissions,
                unrestricted,
            )
        return

    if kind not in _SAFE_TRIGGER_KINDS:
        raise DefinitionError(
            path,
            f"Home Assistant trigger '{kind or 'unknown'}' requires administrator access",
        )

    if kind in {"state", "numeric_state", "zone", "calendar"}:
        _require_entity_read(
            permissions,
            unrestricted,
            definition.get("entity_id"),
            f"{path}.entity_id",
        )
    if kind == "zone":
        _require_entity_read(permissions, unrestricted, definition.get("zone"), f"{path}.zone")
    if kind == "numeric_state":
        if "value_template" in definition:
            raise DefinitionError(
                f"{path}.value_template",
                "templated observers require administrator access",
            )
        for key in ("above", "below"):
            value = definition.get(key)
            if isinstance(value, str) and _looks_like_entity_id(value):
                _require_entity_read(permissions, unrestricted, value, f"{path}.{key}")
    if kind == "time":
        _check_time_trigger_value(
            definition.get("at"),
            f"{path}.at",
            permissions,
            unrestricted,
        )
    if kind == "event":
        event_type = definition.get("event_type")
        event_types = [event_type] if isinstance(event_type, str) else event_type
        if not isinstance(event_types, list) or not event_types:
            raise DefinitionError(f"{path}.event_type", "must be a permitted Home Assistant event")
        for item in event_types:
            if (
                not isinstance(item, str)
                or item == EVENT_STATE_CHANGED
                or item not in SUBSCRIBE_ALLOWLIST
            ):
                raise DefinitionError(
                    f"{path}.event_type",
                    "requires administrator access because Home Assistant does not expose "
                    "this event to normal users",
                )


def _check_native_condition(
    definition: dict[str, Any],
    path: str,
    permissions: Any,
    unrestricted: bool,
) -> None:
    kind = definition.get("condition")
    if kind not in _SAFE_CONDITION_KINDS:
        raise DefinitionError(
            path,
            f"Home Assistant condition '{kind or 'unknown'}' requires administrator access",
        )

    if kind in {"and", "or", "not"}:
        children = definition.get("conditions", [])
        if not isinstance(children, list):
            raise DefinitionError(f"{path}.conditions", "must be a list")
        for index, child in enumerate(children):
            if not isinstance(child, dict) or not is_native_condition(child):
                raise DefinitionError(
                    f"{path}.conditions.{index}", "must be a Home Assistant condition"
                )
            _check_native_condition(
                child,
                f"{path}.conditions.{index}",
                permissions,
                unrestricted,
            )
        return

    if kind in {"state", "numeric_state", "zone"}:
        _require_entity_read(
            permissions,
            unrestricted,
            definition.get("entity_id"),
            f"{path}.entity_id",
        )
    if kind == "state":
        states = definition.get("state")
        for state in states if isinstance(states, list) else [states]:
            if isinstance(state, str) and state.startswith(
                (
                    "input_text.",
                    "input_select.",
                    "input_number.",
                    "input_boolean.",
                    "input_datetime.",
                )
            ):
                _require_entity_read(permissions, unrestricted, state, f"{path}.state")
    if kind == "zone":
        _require_entity_read(permissions, unrestricted, definition.get("zone"), f"{path}.zone")
    if kind == "numeric_state":
        if "value_template" in definition:
            raise DefinitionError(
                f"{path}.value_template",
                "templated observers require administrator access",
            )
        for key in ("above", "below"):
            value = definition.get(key)
            if isinstance(value, str) and _looks_like_entity_id(value):
                _require_entity_read(permissions, unrestricted, value, f"{path}.{key}")
    if kind == "time":
        for key in ("after", "before"):
            value = definition.get(key)
            if isinstance(value, str) and _looks_like_entity_id(value):
                _require_entity_read(permissions, unrestricted, value, f"{path}.{key}")


async def async_validate_native_observation_access(
    hass: HomeAssistant,
    definition: dict[str, Any],
    owner_id: str | None,
) -> None:
    """Fail closed for native configs without a safe non-admin observation boundary."""
    if owner_id is None:
        return
    user = await hass.auth.async_get_user(owner_id)
    if user is None or not getattr(user, "is_active", True):
        raise DefinitionError("owner_id", "does not refer to an active Home Assistant user")
    if user.is_admin:
        return

    permissions = user.permissions
    unrestricted = permissions.access_all_entities(POLICY_READ)

    # Every native fragment is checked before HA compiles template strings into
    # runtime objects. Check recursively rather than maintaining a partial list.
    for key in ("triggers", "conditions", "resolve_when"):
        fragments = definition.get(key, [])
        if isinstance(fragments, dict):
            fragments = [fragments]
        for index, item in enumerate(fragments):
            if isinstance(item, dict) and (is_native_trigger(item) or is_native_condition(item)):
                _require_non_templated(item, f"{key}.{index}")
    definition = _resolve_observer_entities(hass, definition, "definition")

    for index, item in enumerate(definition.get("triggers", [])):
        if isinstance(item, dict) and is_native_trigger(item):
            _check_native_trigger(item, f"triggers.{index}", permissions, unrestricted)

    for index, item in enumerate(definition.get("conditions", [])):
        if isinstance(item, dict) and is_native_condition(item):
            _check_native_condition(item, f"conditions.{index}", permissions, unrestricted)

    resolve_when = definition.get("resolve_when")
    if isinstance(resolve_when, dict) and is_native_trigger(resolve_when):
        _check_native_trigger(resolve_when, "resolve_when", permissions, unrestricted)
