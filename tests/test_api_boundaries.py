"""Ownership and caller-identity boundary tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.conditional_notifications.llm import CreateTool, GetTool
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
async def test_userless_llm_context_stays_non_admin_for_lookup() -> None:
    manager = Mock()
    manager.resolve.return_value = SimpleNamespace(public_dict=lambda: {"id": "shared"})
    tool = GetTool(manager)
    llm_context = SimpleNamespace(context=None)
    tool_input = SimpleNamespace(tool_args={"reference": "shared"})

    result = await tool.async_call(SimpleNamespace(), tool_input, llm_context)

    assert result == {"id": "shared"}
    manager.resolve.assert_called_once_with("shared", None, False, entity_hint=None)


@pytest.mark.asyncio
async def test_userless_llm_create_preserves_shared_context_for_satellites() -> None:
    manager = SimpleNamespace(async_create=AsyncMock(return_value={"id": "record-1"}))
    tool = CreateTool(manager)
    llm_context = SimpleNamespace(context=None)
    definition = {"name": "satellite-created record"}
    tool_input = SimpleNamespace(tool_args={"definition": definition})

    result = await tool.async_call(SimpleNamespace(), tool_input, llm_context)

    assert result == {"id": "record-1"}
    manager.async_create.assert_awaited_once_with(definition, None)


@pytest.mark.asyncio
async def test_authenticated_llm_create_uses_user_as_owner() -> None:
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
