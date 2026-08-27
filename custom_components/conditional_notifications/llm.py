"""Conservative structured LLM API for voice and conversation agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, override

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN, NAME
from .lifecycle import LifecycleNotificationManager
from .manager import AmbiguousReference
from .models import NotificationRecord


async def _identity(hass: HomeAssistant, context: llm.LLMContext) -> tuple[str | None, bool]:
    user_id = context.context.user_id if context.context else None
    if user_id is None:
        return None, False
    user = await hass.auth.async_get_user(user_id)
    return user_id, bool(user and user.is_admin)


class _Tool(llm.Tool):
    manager: LifecycleNotificationManager

    def __init__(self, manager: LifecycleNotificationManager) -> None:
        self.manager = manager

    async def identity(
        self, hass: HomeAssistant, context: llm.LLMContext
    ) -> tuple[str | None, bool]:
        return await _identity(hass, context)

    async def mutation_identity(
        self, hass: HomeAssistant, context: llm.LLMContext
    ) -> tuple[str, bool]:
        """Require a real HA user before an LLM tool may mutate records."""
        user_id, is_admin = await self.identity(hass, context)
        if user_id is None:
            raise HomeAssistantError(
                "Conditional Notifications mutations require an authenticated Home Assistant user"
            )
        return user_id, is_admin

    async def record(
        self, hass: HomeAssistant, args: dict[str, Any], context: llm.LLMContext
    ) -> NotificationRecord | dict[str, Any]:
        user_id, is_admin = await self.identity(hass, context)
        try:
            return self.manager.resolve(
                args["reference"], user_id, is_admin, entity_hint=args.get("entity_hint")
            )
        except AmbiguousReference as err:
            return {"error": "ambiguous", "candidates": err.candidates}


class CreateTool(_Tool):
    name = "CreateConditionalNotification"
    description = "Create a bounded event-driven notification from a normalized definition; never submit automation YAML."
    parameters = vol.Schema({vol.Required("definition"): dict})

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        user_id, _ = await self.mutation_identity(hass, llm_context)
        return await self.manager.async_create(tool_input.tool_args["definition"], user_id)


class ListTool(_Tool):
    name = "ListConditionalNotifications"
    description = "List or search the requesting user's conditional notifications and their status."
    parameters = vol.Schema({vol.Optional("query"): str})

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        user_id, is_admin = await self.identity(hass, llm_context)
        return {
            "notifications": self.manager.list_records(
                user_id, is_admin, tool_input.tool_args.get("query")
            )
        }


class GetTool(_Tool):
    name = "GetConditionalNotification"
    description = "Inspect one notification by exact ID, semantic key, or exact name."
    parameters = vol.Schema({vol.Required("reference"): str, vol.Optional("entity_hint"): str})

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        record = await self.record(hass, tool_input.tool_args, llm_context)
        return record if isinstance(record, dict) else record.public_dict()


class UpdateTool(_Tool):
    name = "UpdateConditionalNotification"
    description = "Update bounded fields on one unambiguous existing notification."
    parameters = vol.Schema(
        {
            vol.Required("reference"): str,
            vol.Required("changes"): dict,
            vol.Optional("entity_hint"): str,
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        await self.mutation_identity(hass, llm_context)
        record = await self.record(hass, tool_input.tool_args, llm_context)
        return (
            record
            if isinstance(record, dict)
            else await self.manager.async_update(record, tool_input.tool_args["changes"])
        )


class ActionTool(_Tool):
    name = "ManageConditionalNotification"
    description = "Pause, resume, enable, disable, re-arm, delete, duplicate, or test one unambiguous notification."
    parameters = vol.Schema(
        {
            vol.Required("reference"): str,
            vol.Required("action"): vol.In(
                [
                    "pause",
                    "resume",
                    "enable",
                    "disable",
                    "rearm",
                    "delete",
                    "duplicate",
                    "test",
                ]
            ),
            vol.Optional("entity_hint"): str,
            vol.Optional("name"): str,
        }
    )

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        user_id, _ = await self.mutation_identity(hass, llm_context)
        args = tool_input.tool_args
        record = await self.record(hass, args, llm_context)
        if isinstance(record, dict):
            return record
        action = args["action"]
        if action == "pause":
            return await self.manager.async_set_paused(record, True)
        if action == "resume":
            return await self.manager.async_set_paused(record, False)
        if action == "enable":
            return await self.manager.async_set_enabled(record, True)
        if action == "disable":
            return await self.manager.async_set_enabled(record, False)
        if action == "rearm":
            return await self.manager.async_rearm(record)
        if action == "delete":
            return await self.manager.async_delete(record)
        if action == "test":
            return await self.manager.async_test(record)
        return await self.manager.async_duplicate(record, user_id, args.get("name"))


class ConditionalNotificationsAPI(llm.API):
    def __init__(self, *, hass: HomeAssistant, manager: LifecycleNotificationManager) -> None:
        super().__init__(hass=hass, id=DOMAIN, name=NAME)
        self.manager = manager

    @override
    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "Use these tools for requests to notify the user when an event happens. Use only bounded definitions "
                "with entity IDs; never create automation YAML. Mutations must use an exact, unambiguous reference."
            ),
            llm_context=llm_context,
            tools=[
                CreateTool(self.manager),
                ListTool(self.manager),
                GetTool(self.manager),
                UpdateTool(self.manager),
                ActionTool(self.manager),
            ],
        )


def async_register_llm_api(
    hass: HomeAssistant, manager: LifecycleNotificationManager
) -> Callable[[], None]:
    return llm.async_register_api(hass, ConditionalNotificationsAPI(hass=hass, manager=manager))
