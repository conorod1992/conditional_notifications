"""Authorization boundaries for owned and system conditional notifications."""

from __future__ import annotations

from typing import Any

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.auth.permissions.events import SUBSCRIBE_ALLOWLIST
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .models import NotificationRecord
from .validation import DefinitionError

NAMED_TRIGGER_ADMIN_SCOPE = "_conditional_notifications_admin_scope"


class MutationForbidden(HomeAssistantError):
    """A caller attempted to mutate a record outside its ownership scope."""


def can_mutate_record(
    record: NotificationRecord, user_id: str | None, is_admin: bool
) -> bool:
    """Return whether a caller may mutate a record.

    System-owned records remain shared/readable, but authenticated non-admin users
    may not change or execute them. A genuinely userless caller is treated as
    system context so voice/automation flows which created a shared record can
    continue to manage it.
    """
    if is_admin:
        return True
    if record.owner_id is None:
        return user_id is None
    return record.owner_id == user_id


def require_mutation_access(
    record: NotificationRecord, user_id: str | None, is_admin: bool
) -> None:
    """Reject mutations outside the record's ownership boundary."""
    if can_mutate_record(record, user_id, is_admin):
        return
    if record.owner_id is None:
        raise MutationForbidden(
            "System-owned conditional notifications can only be changed or executed "
            "by an administrator or system context"
        )
    raise MutationForbidden("This conditional notification belongs to another user")


def _observed_definitions(
    definition: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    triggers = definition.get("triggers")
    if isinstance(triggers, list):
        result.extend(
            (f"triggers.{index}", item)
            for index, item in enumerate(triggers)
            if isinstance(item, dict)
        )
    conditions = definition.get("conditions")
    if isinstance(conditions, list):
        result.extend(
            (f"conditions.{index}", item)
            for index, item in enumerate(conditions)
            if isinstance(item, dict)
        )
    resolve_when = definition.get("resolve_when")
    if isinstance(resolve_when, dict):
        result.append(("resolve_when", resolve_when))
    return result


async def async_validate_observation_access(
    hass: HomeAssistant, definition: dict[str, Any], owner_id: str | None
) -> None:
    """Ensure a user-owned definition cannot observe data the owner may not read."""
    if owner_id is None:
        return

    user = await hass.auth.async_get_user(owner_id)
    if user is None or not getattr(user, "is_active", True):
        raise DefinitionError("owner_id", "does not refer to an active Home Assistant user")
    if user.is_admin:
        return

    permissions = user.permissions
    unrestricted_entities = permissions.access_all_entities(POLICY_READ)

    for path, item in _observed_definitions(definition):
        kind = item.get("type")
        if kind == "event":
            event_type = item.get("event_type")
            if isinstance(event_type, str) and (
                event_type == EVENT_STATE_CHANGED or event_type not in SUBSCRIBE_ALLOWLIST
            ):
                raise DefinitionError(
                    f"{path}.event_type",
                    "requires administrator access because Home Assistant does not expose "
                    "this event to normal users",
                )
            continue

        if kind not in {"state", "numeric_state", "zone"}:
            continue

        fields = ["entity_id"]
        if kind == "zone":
            fields.append("zone_entity_id")
        for field in fields:
            entity_id = item.get(field)
            if not isinstance(entity_id, str) or unrestricted_entities:
                continue
            if not permissions.check_entity(entity_id, POLICY_READ):
                raise DefinitionError(
                    f"{path}.{field}",
                    "is not readable by the notification owner",
                )
