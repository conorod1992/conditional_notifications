"""Tests for bounded Companion App delivery options."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.delivery import async_deliver, merge_delivery
from custom_components.conditional_notifications.validation import (
    DefinitionError,
    validate_definition,
)


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
async def test_modern_notify_entity_does_not_receive_unsupported_companion_data() -> None:
    async_call = AsyncMock()
    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": ["notify.phone"],
                "companion": {"url": "/lovelace/security"},
            }
        },
    )

    result = await async_deliver(hass, record, "Door", "The door opened", {})

    async_call.assert_awaited_once_with(
        "notify",
        "send_message",
        {"title": "Door", "message": "The door opened"},
        blocking=True,
        target={"entity_id": "notify.phone"},
    )
    assert result == [{"channel": "notify.phone", "success": True}]


@pytest.mark.asyncio
async def test_legacy_mobile_service_receives_only_bounded_companion_data() -> None:
    async_call = AsyncMock()
    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": [],
                "notify_services": ["notify.mobile_app_phone"],
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
        "mobile_app_phone",
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
    )
    assert result == [{"channel": "notify.mobile_app_phone", "success": True}]


@pytest.mark.asyncio
async def test_delivery_targets_run_concurrently() -> None:
    started: list[tuple[str, str]] = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def async_call(domain, service, data, **kwargs):
        del data, kwargs
        started.append((domain, service))
        if len(started) == 3:
            all_started.set()
        await release.wait()

    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": ["notify.phone_one", "notify.phone_two"],
                "notify_services": ["notify.legacy_phone"],
            }
        },
    )

    task = asyncio.create_task(async_deliver(hass, record, "Door", "The door opened", {}))
    await asyncio.wait_for(all_started.wait(), timeout=1)
    assert len(started) == 3

    release.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert all(item["success"] for item in result)


@pytest.mark.asyncio
async def test_concurrent_delivery_failure_does_not_cancel_siblings() -> None:
    async def async_call(domain, service, data, **kwargs):
        del domain, data, kwargs
        if service == "broken":
            raise RuntimeError("provider unavailable")

    hass = SimpleNamespace(services=SimpleNamespace(async_call=async_call))
    record = SimpleNamespace(
        id="record-id",
        definition={
            "delivery": {
                "use_defaults": False,
                "persistent_notification": False,
                "notify_entities": [],
                "notify_services": ["notify.good", "notify.broken"],
            }
        },
    )

    result = await async_deliver(hass, record, "Door", "The door opened", {})
    by_channel = {item["channel"]: item for item in result}
    assert by_channel["notify.good"]["success"] is True
    assert by_channel["notify.broken"]["success"] is False
    assert "provider unavailable" in by_channel["notify.broken"]["error"]


@pytest.mark.parametrize(
    "companion",
    [
        {"url": "javascript:alert(1)"},
        {"url": "//evil.example/path"},
        {"actions": [{"title": "Bad", "uri": "intent://unsafe"}]},
        {"actions": [{"title": "Bad", "uri": "//evil.example/path"}]},
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
