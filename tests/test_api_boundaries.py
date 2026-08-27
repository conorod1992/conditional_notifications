"""Ownership and caller-identity boundary tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.conditional_notifications.llm import CreateTool
from custom_components.conditional_notifications.services import _identity as service_identity
from custom_components.conditional_notifications.websocket import _resolve as websocket_resolve


def test_websocket_resolution_uses_authenticated_connection_identity() -> None:
    manager = Mock()
    expected = object()
    manager.resolve.return_value = expected
    connection = SimpleNamespace(user=SimpleNamespace(id="user-1", is_admin=False))

    result = websocket_resolve(manager, connection, "front door")

    assert result is expected
    manager.resolve.assert_called_once_with("front door", "user-1", False)


@pytest.mark.asyncio
async def test_service_identity_preserves_internal_and_user_boundaries() -> None:
    hass = SimpleNamespace(
        auth=SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
        )
    )

    internal = await service_identity(hass, SimpleNamespace(context=SimpleNamespace(user_id=None)))
    user = await service_identity(hass, SimpleNamespace(context=SimpleNamespace(user_id="user-1")))

    assert internal == (None, True)
    assert user == ("user-1", False)


@pytest.mark.asyncio
async def test_llm_mutation_without_authenticated_user_is_rejected() -> None:
    manager = SimpleNamespace(async_create=AsyncMock())
    tool = CreateTool(manager)
    hass = SimpleNamespace()
    llm_context = SimpleNamespace(context=None)
    tool_input = SimpleNamespace(tool_args={"definition": {"name": "unsafe shared record"}})

    with pytest.raises(HomeAssistantError, match="authenticated Home Assistant user"):
        await tool.async_call(hass, tool_input, llm_context)

    manager.async_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_mutation_uses_authenticated_user_as_owner() -> None:
    manager = SimpleNamespace(async_create=AsyncMock(return_value={"id": "record-1"}))
    tool = CreateTool(manager)
    hass = SimpleNamespace(
        auth=SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))
        )
    )
    llm_context = SimpleNamespace(context=SimpleNamespace(user_id="user-1"))
    definition = {"name": "owned record"}
    tool_input = SimpleNamespace(tool_args={"definition": definition})

    result = await tool.async_call(hass, tool_input, llm_context)

    assert result == {"id": "record-1"}
    manager.async_create.assert_awaited_once_with(definition, "user-1")
