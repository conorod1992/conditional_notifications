"""Response-capable Home Assistant actions."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .lifecycle import LifecycleNotificationManager
from .manager import AmbiguousReference
from .security import NAMED_TRIGGER_ADMIN_SCOPE, require_mutation_access

REFERENCE_SCHEMA = {vol.Required("notification_id"): cv.string}


async def _identity(hass: HomeAssistant, call: ServiceCall) -> tuple[str | None, bool]:
    user_id = call.context.user_id
    if user_id is None:
        return None, True
    user = await hass.auth.async_get_user(user_id)
    return user_id, bool(user and user.is_admin)


def async_register_services(hass: HomeAssistant, manager: LifecycleNotificationManager) -> None:
    """Register the bounded action surface."""

    async def create(call: ServiceCall) -> dict[str, Any]:
        user_id, _ = await _identity(hass, call)
        return await manager.async_create(dict(call.data["definition"]), user_id)

    async def list_records(call: ServiceCall) -> dict[str, Any]:
        user_id, is_admin = await _identity(hass, call)
        return {"notifications": manager.list_records(user_id, is_admin, call.data.get("query"))}

    async def invoke(call: ServiceCall) -> dict[str, Any]:
        user_id, is_admin = await _identity(hass, call)
        try:
            record = manager.resolve(call.data["notification_id"], user_id, is_admin)
        except AmbiguousReference as err:
            return {"error": "ambiguous", "candidates": err.candidates}
        action = call.service
        if action == "get":
            return record.public_dict()
        if action == "duplicate":
            return await manager.async_duplicate(record, user_id, call.data.get("name"))

        require_mutation_access(record, user_id, is_admin)
        if action == "update":
            return await manager.async_update(record, dict(call.data["changes"]))
        if action == "delete":
            return await manager.async_delete(record)
        if action == "pause":
            return await manager.async_set_paused(record, True)
        if action == "resume":
            return await manager.async_set_paused(record, False)
        if action == "enable":
            return await manager.async_set_enabled(record, True)
        if action == "disable":
            return await manager.async_set_enabled(record, False)
        if action == "rearm":
            return await manager.async_rearm(record)
        if action == "test":
            return await manager.async_test(record)
        if action == "trigger_now":
            return await manager.async_trigger_now(record)
        raise ValueError(f"Unsupported action {action}")

    async def fire_named(call: ServiceCall) -> dict[str, Any]:
        user_id, is_admin = await _identity(hass, call)
        event_data: dict[str, Any] = {
            "trigger_id": call.data["trigger_id"],
            "data": call.data.get("data", {}),
        }
        # Named-trigger listeners use the Home Assistant context for same-owner
        # calls. Only this service adds the reserved admin marker, so ordinary
        # user-supplied data cannot elevate a named trigger to another owner.
        if user_id is not None and is_admin:
            event_data[NAMED_TRIGGER_ADMIN_SCOPE] = True
        hass.bus.async_fire(
            f"{DOMAIN}_named_trigger",
            event_data,
            context=call.context,
        )
        return {"trigger_id": call.data["trigger_id"], "fired": True}

    async def clear_history(call: ServiceCall) -> dict[str, Any]:
        user_id, is_admin = await _identity(hass, call)
        notification_id = call.data.get("notification_id")
        if notification_id:
            record = manager.resolve(notification_id, user_id, is_admin)
            require_mutation_access(record, user_id, is_admin)
            notification_id = record.id
        elif not is_admin:
            raise vol.Invalid("Only administrators may clear all history")
        return await manager.async_clear_history(notification_id)

    hass.services.async_register(
        DOMAIN,
        "create",
        create,
        schema=vol.Schema({vol.Required("definition"): dict}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "list",
        list_records,
        schema=vol.Schema({vol.Optional("query"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    for action in (
        "get",
        "delete",
        "pause",
        "resume",
        "enable",
        "disable",
        "rearm",
        "test",
        "trigger_now",
    ):
        hass.services.async_register(
            DOMAIN,
            action,
            invoke,
            schema=vol.Schema(REFERENCE_SCHEMA),
            supports_response=(
                SupportsResponse.ONLY if action == "get" else SupportsResponse.OPTIONAL
            ),
        )
    hass.services.async_register(
        DOMAIN,
        "update",
        invoke,
        schema=vol.Schema({**REFERENCE_SCHEMA, vol.Required("changes"): dict}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "duplicate",
        invoke,
        schema=vol.Schema({**REFERENCE_SCHEMA, vol.Optional("name"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "fire_named_trigger",
        fire_named,
        schema=vol.Schema(
            {vol.Required("trigger_id"): cv.string, vol.Optional("data", default={}): dict}
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "clear_history",
        clear_history,
        schema=vol.Schema({vol.Optional("notification_id"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for action in (
        "create",
        "get",
        "list",
        "update",
        "delete",
        "pause",
        "resume",
        "enable",
        "disable",
        "rearm",
        "duplicate",
        "test",
        "trigger_now",
        "fire_named_trigger",
        "clear_history",
    ):
        hass.services.async_remove(DOMAIN, action)
