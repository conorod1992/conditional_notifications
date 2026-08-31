"""Authorization checks for Home Assistant-native trigger and condition fragments."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.auth.permissions.events import SUBSCRIBE_ALLOWLIST
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant

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
        _require_entity_read(
            permissions, unrestricted, definition.get("zone"), f"{path}.zone"
        )
    if kind == "numeric_state":
        for key in ("above", "below"):
            value = definition.get(key)
            if isinstance(value, str) and _looks_like_entity_id(value):
                _require_entity_read(
                    permissions, unrestricted, value, f"{path}.{key}"
                )
    if kind == "time":
        at = definition.get("at")
        if isinstance(at, dict):
            _require_entity_read(
                permissions,
                unrestricted,
                at.get("entity_id"),
                f"{path}.at.entity_id",
            )
        elif isinstance(at, str) and _looks_like_entity_id(at):
            _require_entity_read(permissions, unrestricted, at, f"{path}.at")
    if kind == "event":
        event_type = definition.get("event_type")
        event_types = [event_type] if isinstance(event_type, str) else event_type
        if not isinstance(event_types, list) or not event_types:
            raise DefinitionError(
                f"{path}.event_type", "must be a permitted Home Assistant event"
            )
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
    if kind == "zone":
        _require_entity_read(
            permissions, unrestricted, definition.get("zone"), f"{path}.zone"
        )
    if kind == "numeric_state":
        for key in ("above", "below"):
            value = definition.get(key)
            if isinstance(value, str) and _looks_like_entity_id(value):
                _require_entity_read(
                    permissions, unrestricted, value, f"{path}.{key}"
                )


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
        raise DefinitionError(
            "owner_id", "does not refer to an active Home Assistant user"
        )
    if user.is_admin:
        return

    permissions = user.permissions
    unrestricted = permissions.access_all_entities(POLICY_READ)

    for index, item in enumerate(definition.get("triggers", [])):
        if isinstance(item, dict) and is_native_trigger(item):
            _check_native_trigger(
                item, f"triggers.{index}", permissions, unrestricted
            )

    for index, item in enumerate(definition.get("conditions", [])):
        if isinstance(item, dict) and is_native_condition(item):
            _check_native_condition(
                item, f"conditions.{index}", permissions, unrestricted
            )

    resolve_when = definition.get("resolve_when")
    if isinstance(resolve_when, dict) and is_native_trigger(resolve_when):
        _check_native_trigger(resolve_when, "resolve_when", permissions, unrestricted)
