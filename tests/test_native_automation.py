"""Tests for Home Assistant-native trigger and condition compatibility."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.conditional_notifications.conditions import async_evaluate_conditions
from custom_components.conditional_notifications.native_automation import (
    is_native_trigger,
    legacy_trigger_view,
    trigger_kind,
)
from custom_components.conditional_notifications.native_context import (
    CURRENT_CONDITION_CHECKERS,
    CURRENT_TRIGGER,
)
from custom_components.conditional_notifications.native_security import (
    async_validate_native_observation_access,
)
from custom_components.conditional_notifications.native_validation import validate_definition
from custom_components.conditional_notifications.validation import DefinitionError


def _definition() -> dict:
    return {
        "name": "Native test",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"},
            {"type": "named", "trigger_id": "external_signal"},
        ],
        "conditions": [
            {
                "condition": "or",
                "conditions": [
                    {"condition": "state", "entity_id": "person.conor", "state": "home"},
                    {"condition": "sun", "after": "sunset"},
                ],
            }
        ],
        "title": "Door",
        "message": "Opened",
        "repeat_policy": "once",
        "delivery": {"use_defaults": True},
    }


def test_native_fragments_round_trip_without_storage_migration() -> None:
    normalized = validate_definition(_definition())
    assert normalized["triggers"][0] == {
        "trigger": "state",
        "entity_id": "binary_sensor.door",
        "to": "on",
    }
    assert normalized["triggers"][1]["type"] == "named"
    assert normalized["conditions"][0]["condition"] == "or"


def test_native_trigger_groups_are_one_supported_fragment() -> None:
    definition = _definition()
    definition["triggers"][0] = {
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.a", "to": "on"},
            {"trigger": "state", "entity_id": "binary_sensor.b", "to": "on"},
        ]
    }
    normalized = validate_definition(definition)
    group = normalized["triggers"][0]
    assert is_native_trigger(group)
    assert trigger_kind(group) == "group"
    assert len(group["triggers"]) == 2


def test_native_fragments_are_structurally_bounded_before_ha_validation() -> None:
    definition = _definition()
    definition["triggers"][0] = {
        "trigger": "numeric_state",
        "entity_id": "sensor.temperature",
        "above": float("nan"),
    }
    with pytest.raises(DefinitionError, match="non-finite"):
        validate_definition(definition)

    definition = _definition()
    definition["triggers"][0] = {
        "trigger": "state",
        "platform": "state",
        "entity_id": "binary_sensor.door",
        "to": "on",
    }
    with pytest.raises(DefinitionError, match="exactly one"):
        validate_definition(definition)


def test_simple_native_state_projects_for_current_state_lifecycle() -> None:
    native = {
        "trigger": "state",
        "entity_id": "binary_sensor.door",
        "from": "off",
        "to": "on",
        "for": {"minutes": 2},
    }
    assert legacy_trigger_view(native) == {
        "type": "state",
        "entity_id": "binary_sensor.door",
        "from": "off",
        "to": "on",
        "for": {"minutes": 2},
    }
    assert legacy_trigger_view({"trigger": "time", "at": "08:00:00"}) is None


def test_native_condition_uses_final_trigger_variables_and_fails_closed() -> None:
    class Checker:
        def async_check(self, *, variables=None):
            return variables["trigger"]["id"] == "door"

    condition = {"condition": "trigger", "id": ["door"]}
    trigger_token = CURRENT_TRIGGER.set({"id": "door"})
    checker_token = CURRENT_CONDITION_CHECKERS.set({id(condition): Checker()})
    try:
        passed, details = async_evaluate_conditions(SimpleNamespace(), [condition], datetime.now())
    finally:
        CURRENT_CONDITION_CHECKERS.reset(checker_token)
        CURRENT_TRIGGER.reset(trigger_token)
    assert passed is True
    assert details == [{"type": "trigger", "native": True, "passed": True}]

    passed, details = async_evaluate_conditions(SimpleNamespace(), [condition], datetime.now())
    assert passed is False
    assert details[0]["error"] == "condition checker is unavailable"


@pytest.mark.asyncio
async def test_non_admin_native_security_is_conservative() -> None:
    class Permissions:
        def access_all_entities(self, _policy):
            return False

        def check_entity(self, entity_id, _policy):
            return entity_id == "binary_sensor.allowed"

    user = SimpleNamespace(is_active=True, is_admin=False, permissions=Permissions())
    hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)))

    allowed = _definition()
    allowed["triggers"] = [{"trigger": "state", "entity_id": "binary_sensor.allowed", "to": "on"}]
    allowed["conditions"] = []
    await async_validate_native_observation_access(hass, allowed, "user-1")

    denied_entity = _definition()
    denied_entity["triggers"] = [
        {"trigger": "state", "entity_id": "binary_sensor.secret", "to": "on"}
    ]
    with pytest.raises(DefinitionError, match="not readable"):
        await async_validate_native_observation_access(hass, denied_entity, "user-1")

    advanced = _definition()
    advanced["triggers"] = [{"trigger": "template", "value_template": "{{ true }}"}]
    with pytest.raises(DefinitionError, match="administrator access"):
        await async_validate_native_observation_access(hass, advanced, "user-1")
