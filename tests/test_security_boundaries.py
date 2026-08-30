"""Regression tests for ownership and observation authorization boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.const import DOMAIN
from custom_components.conditional_notifications.llm import UpdateTool
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.security import (
    NAMED_TRIGGER_ADMIN_SCOPE,
    async_validate_observation_access,
    can_mutate_record,
)
from custom_components.conditional_notifications.triggers import (
    RuntimeSubscriptions,
    attach_trigger,
)
from custom_components.conditional_notifications.validation import DefinitionError
from homeassistant.const import EVENT_COMPONENT_LOADED, EVENT_STATE_CHANGED


def definition(**extra):
    data = {
        "name": "Watch",
        "triggers": [
            {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}
        ],
        "conditions": [],
        "title": "Watch",
        "message": "Matched",
        "repeat_policy": "every",
        "delivery": {"use_defaults": True},
    }
    data.update(extra)
    return data


def record(owner_id: str | None) -> NotificationRecord:
    return NotificationRecord.create(definition(), owner_id)


def permissions(*, readable: set[str] | None = None, all_entities: bool = False):
    readable = readable or set()
    return SimpleNamespace(
        access_all_entities=lambda _policy: all_entities,
        check_entity=lambda entity_id, _policy: entity_id in readable,
    )


def user(
    *,
    is_admin: bool = False,
    readable: set[str] | None = None,
    all_entities: bool = False,
):
    return SimpleNamespace(
        is_admin=is_admin,
        is_active=True,
        permissions=permissions(readable=readable, all_entities=all_entities),
    )


def test_system_records_are_readable_but_not_user_mutable() -> None:
    shared = record(None)
    owned = record("user-1")

    assert not can_mutate_record(shared, "user-1", False)
    assert can_mutate_record(shared, "admin-1", True)
    assert can_mutate_record(shared, None, False)
    assert can_mutate_record(owned, "user-1", False)
    assert not can_mutate_record(owned, "user-2", False)


@pytest.mark.asyncio
async def test_user_definition_cannot_observe_unread_entity() -> None:
    hass = SimpleNamespace(
        auth=SimpleNamespace(
            async_get_user=AsyncMock(return_value=user(readable={"binary_sensor.motion"}))
        )
    )
    restricted = definition(
        conditions=[{"type": "state", "entity_id": "person.private", "state": "home"}]
    )

    with pytest.raises(DefinitionError, match="not readable") as err:
        await async_validate_observation_access(hass, restricted, "user-1")

    assert err.value.field == "conditions.0.entity_id"


@pytest.mark.asyncio
async def test_zone_definition_requires_read_access_to_zone_too() -> None:
    hass = SimpleNamespace(
        auth=SimpleNamespace(
            async_get_user=AsyncMock(return_value=user(readable={"person.me"}))
        )
    )
    restricted = definition(
        triggers=[
            {
                "type": "zone",
                "entity_id": "person.me",
                "zone_entity_id": "zone.private",
                "event": "enter",
            }
        ]
    )

    with pytest.raises(DefinitionError, match="not readable") as err:
        await async_validate_observation_access(hass, restricted, "user-1")

    assert err.value.field == "triggers.0.zone_entity_id"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [EVENT_STATE_CHANGED, "secret_custom_event"])
async def test_non_admin_event_watches_follow_ha_subscription_boundary(event_type: str) -> None:
    hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user(all_entities=True)))
    )
    restricted = definition(triggers=[{"type": "event", "event_type": event_type}])

    with pytest.raises(DefinitionError, match="requires administrator access"):
        await async_validate_observation_access(hass, restricted, "user-1")


@pytest.mark.asyncio
async def test_non_admin_can_watch_home_assistant_safe_event() -> None:
    hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user(all_entities=True)))
    )
    allowed = definition(
        triggers=[{"type": "event", "event_type": EVENT_COMPONENT_LOADED}]
    )

    await async_validate_observation_access(hass, allowed, "user-1")


@pytest.mark.asyncio
async def test_admin_and_system_definitions_keep_full_observation_scope() -> None:
    admin_hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user(is_admin=True)))
    )
    arbitrary = definition(
        triggers=[{"type": "event", "event_type": "secret_custom_event"}]
    )

    await async_validate_observation_access(admin_hass, arbitrary, "admin-1")
    await async_validate_observation_access(SimpleNamespace(), arbitrary, None)


class FakeBus:
    def __init__(self) -> None:
        self.listener = None

    def async_listen(self, _event_type, listener):
        self.listener = listener
        return lambda: None


def named_runtime(owner_id: str | None):
    bus = FakeBus()
    stored = SimpleNamespace(owner_id=owner_id, revision=1)
    manager = SimpleNamespace(store=SimpleNamespace(records={"record-1": stored}))
    hass = SimpleNamespace(bus=bus, data={DOMAIN: {"manager": manager}})
    return RuntimeSubscriptions(hass, "record-1", 1), bus


def event(user_id: str | None, *, admin_scope: bool = False):
    data = {"trigger_id": "door", "data": {"source": "test"}}
    if admin_scope:
        data[NAMED_TRIGGER_ADMIN_SCOPE] = True
    return SimpleNamespace(data=data, context=SimpleNamespace(user_id=user_id))


def test_named_trigger_is_scoped_to_owner_unless_admin_or_system() -> None:
    runtime, bus = named_runtime("user-1")
    accepted = []
    attach_trigger(runtime, {"type": "named", "trigger_id": "door"}, 0, accepted.append)

    assert bus.listener is not None
    bus.listener(event("user-2"))
    assert accepted == []

    bus.listener(event("user-1"))
    assert len(accepted) == 1

    bus.listener(event("admin-1", admin_scope=True))
    assert len(accepted) == 2
    assert NAMED_TRIGGER_ADMIN_SCOPE not in accepted[-1]["event_data"]

    bus.listener(event(None))
    assert len(accepted) == 3


def test_named_trigger_does_not_allow_user_to_activate_system_record() -> None:
    runtime, bus = named_runtime(None)
    accepted = []
    attach_trigger(runtime, {"type": "named", "trigger_id": "door"}, 0, accepted.append)

    assert bus.listener is not None
    bus.listener(event("user-1"))
    assert accepted == []

    bus.listener(event("admin-1", admin_scope=True))
    assert len(accepted) == 1


@pytest.mark.asyncio
async def test_authenticated_llm_cannot_mutate_shared_record() -> None:
    shared = record(None)
    manager = SimpleNamespace(
        resolve=lambda *_args, **_kwargs: shared,
        async_update=AsyncMock(),
    )
    tool = UpdateTool(manager)
    hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user()))
    )
    llm_context = SimpleNamespace(context=SimpleNamespace(user_id="user-1"))
    tool_input = SimpleNamespace(
        tool_args={"reference": shared.id, "changes": {"message": "changed"}}
    )

    result = await tool.async_call(hass, tool_input, llm_context)

    assert result["error"] == "forbidden"
    manager.async_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_userless_llm_can_manage_system_owned_record() -> None:
    shared = record(None)
    updated = {"id": shared.id, "message": "changed"}
    manager = SimpleNamespace(
        resolve=lambda *_args, **_kwargs: shared,
        async_update=AsyncMock(return_value=updated),
    )
    tool = UpdateTool(manager)
    llm_context = SimpleNamespace(context=None)
    tool_input = SimpleNamespace(
        tool_args={"reference": shared.id, "changes": {"message": "changed"}}
    )

    result = await tool.async_call(SimpleNamespace(), tool_input, llm_context)

    assert result == updated
    manager.async_update.assert_awaited_once()
