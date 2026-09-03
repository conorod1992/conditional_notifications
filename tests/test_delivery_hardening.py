"""Delivery hardening tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.conditional_notifications.delivery import async_deliver


@pytest.mark.asyncio
async def test_delivery_targets_run_concurrently():
    """A blocked target must not serialize otherwise independent provider calls."""
    hass = MagicMock()
    hass.auth.async_get_user = AsyncMock(return_value=None)

    started: list[tuple[str, str]] = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def service_call(domain, service, data, **kwargs):
        del data, kwargs
        started.append((domain, service))
        if len(started) == 3:
            all_started.set()
        await release.wait()

    hass.services.async_call = service_call
    record = SimpleNamespace(
        id="example",
        owner_id=None,
        definition={
            "delivery": {
                "use_defaults": False,
                "notify_entities": ["notify.phone_one", "notify.phone_two"],
                "notify_services": ["notify.legacy_phone"],
                "assist_satellites": [],
                "persistent_notification": False,
            }
        },
    )

    task = asyncio.create_task(async_deliver(hass, record, "Title", "Message", {}))
    await asyncio.wait_for(all_started.wait(), timeout=1)
    assert len(started) == 3

    release.set()
    results = await asyncio.wait_for(task, timeout=1)
    assert all(result["success"] for result in results)


@pytest.mark.asyncio
async def test_delivery_failure_isolated_per_concurrent_target():
    """One concurrent provider failure must not cancel successful siblings."""
    hass = MagicMock()
    hass.auth.async_get_user = AsyncMock(return_value=None)

    async def service_call(domain, service, data, **kwargs):
        del domain, data, kwargs
        if service == "broken":
            raise RuntimeError("provider unavailable")

    hass.services.async_call = service_call
    record = SimpleNamespace(
        id="example",
        owner_id=None,
        definition={
            "delivery": {
                "use_defaults": False,
                "notify_entities": [],
                "notify_services": ["notify.good", "notify.broken"],
                "assist_satellites": [],
                "persistent_notification": False,
            }
        },
    )

    results = await async_deliver(hass, record, "Title", "Message", {})
    by_channel = {result["channel"]: result for result in results}
    assert by_channel["notify.good"]["success"] is True
    assert by_channel["notify.broken"]["success"] is False
    assert "provider unavailable" in by_channel["notify.broken"]["error"]
