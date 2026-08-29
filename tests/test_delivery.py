"""Delivery channel tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.delivery import async_deliver, merge_delivery


def test_merge_delivery_keeps_entity_legacy_and_satellite_channels() -> None:
    override = {
        "use_defaults": False,
        "persistent_notification": False,
        "notify_entities": ["notify.phone"],
        "notify_services": ["notify.legacy_phone"],
        "assist_satellites": ["assist_satellite.kitchen"],
    }
    assert merge_delivery({}, override) == {
        "persistent_notification": False,
        "notify_entities": ["notify.phone"],
        "notify_services": ["notify.legacy_phone"],
        "assist_satellites": ["assist_satellite.kitchen"],
    }


@pytest.mark.asyncio
async def test_notify_entity_uses_send_message_target() -> None:
    async_call = AsyncMock()
    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": ["notify.conors_phone"],
            }
        },
    )

    result = await async_deliver(hass, record, "Door", "The door opened", {})

    async_call.assert_awaited_once_with(
        "notify",
        "send_message",
        {"title": "Door", "message": "The door opened"},
        blocking=True,
        target={"entity_id": "notify.conors_phone"},
    )
    assert result == [{"channel": "notify.conors_phone", "success": True}]


@pytest.mark.asyncio
async def test_assist_satellite_uses_announce_and_speaks_message_only() -> None:
    async_call = AsyncMock()
    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "assist_satellites": ["assist_satellite.kitchen"],
            }
        },
    )

    result = await async_deliver(hass, record, "Door", "The door opened", {})

    async_call.assert_awaited_once_with(
        "assist_satellite",
        "announce",
        {"message": "The door opened"},
        blocking=True,
        target={"entity_id": "assist_satellite.kitchen"},
    )
    assert result == [{"channel": "assist_satellite.kitchen", "success": True}]
