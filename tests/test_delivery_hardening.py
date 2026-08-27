"""Tests for bounded Companion App delivery options."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.delivery import async_deliver, merge_delivery
from custom_components.conditional_notifications.validation import DefinitionError, validate_definition


def definition(delivery: dict) -> dict:
    return {
        "name": "Door alert",
        "triggers": [{"type": "state", "entity_id": "binary_sensor.door", "to": "on"}],
        "title": "Door",
        "message": "The door opened",
        "delivery": delivery,
    }


def test_companion_options_survive_default_target_merge() -> None:
    defaults = {
        "persistent_notification": False,
        "notify_entities": ["notify.phone"],
        "notify_services": [],
    }
    override = {
        "use_defaults": True,
        "companion": {"url": "/lovelace/security"},
    }

    merged = merge_delivery(defaults, override)

    assert merged["notify_entities"] == ["notify.phone"]
    assert merged["companion"] == {"url": "/lovelace/security"}
    assert "companion" not in defaults


@pytest.mark.asyncio
async def test_notify_payload_contains_only_bounded_companion_data() -> None:
    async_call = AsyncMock()
    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": ["notify.phone"],
                "companion": {
                    "url": "/lovelace/security",
                    "actions": [
                        {"title": "Silence", "action": "SILENCE_ALERT"},
                        {"title": "Open cameras", "uri": "/lovelace/cameras"},
                    ],
                },
            }
        },
    )

    result = await async_deliver(hass, record, "Door", "The door opened", {})

    async_call.assert_awaited_once_with(
        "notify",
        "send_message",
        {
            "title": "Door",
            "message": "The door opened",
            "data": {
                "url": "/lovelace/security",
                "actions": [
                    {"action": "SILENCE_ALERT", "title": "Silence"},
                    {"action": "URI", "title": "Open cameras", "uri": "/lovelace/cameras"},
                ],
            },
        },
        blocking=True,
        target={"entity_id": "notify.phone"},
    )
    assert result == [{"channel": "notify.phone", "success": True}]


@pytest.mark.parametrize(
    "companion",
    [
        {"url": "javascript:alert(1)"},
        {"actions": [{"title": "Bad", "uri": "intent://unsafe"}]},
        {"actions": [{"title": "Bad", "action": "BAD ACTION"}]},
        {"actions": [{"title": "Reserved", "action": "URI"}]},
        {"actions": [{"title": str(index), "action": f"ACTION_{index}"} for index in range(4)]},
        {"arbitrary": {"payload": True}},
    ],
)
def test_validation_rejects_unbounded_or_unsafe_companion_payloads(companion: dict) -> None:
    with pytest.raises(DefinitionError):
        validate_definition(definition({"use_defaults": True, "companion": companion}))


def test_validation_normalizes_safe_companion_buttons() -> None:
    normalized = validate_definition(
        definition(
            {
                "use_defaults": True,
                "companion": {
                    "url": " https://example.com/status ",
                    "actions": [
                        {"title": " Ack ", "action": "ACK:DOOR-1"},
                        {"title": "Open", "uri": "/lovelace/security"},
                    ],
                },
            }
        )
    )

    assert normalized["delivery"]["companion"] == {
        "url": "https://example.com/status",
        "actions": [
            {"title": "Ack", "action": "ACK:DOOR-1"},
            {"title": "Open", "uri": "/lovelace/security"},
        ],
    }
