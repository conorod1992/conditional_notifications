"""Focused tests for lifecycle persistence coalescing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.conditional_notifications.optimized_manager import (
    LifecycleNotificationManager,
)


class _FakeStore:
    def __init__(self, record: Any) -> None:
        self.records = {record.id: record}
        self.save_count = 0

    async def async_save(self) -> None:
        self.save_count += 1


class _FakeHass:
    data: dict[str, Any] = {}

    def async_create_task(self, coroutine: Any, *, eager_start: bool = False) -> asyncio.Task[Any]:
        del eager_start
        return asyncio.create_task(coroutine)


def _manager(record: Any) -> tuple[LifecycleNotificationManager, _FakeStore, list[str]]:
    manager = object.__new__(LifecycleNotificationManager)
    manager.hass = _FakeHass()
    manager._locks = {}
    manager._tasks = set()
    manager._shutting_down = False
    manager._ignored_persistence_tasks = {}
    manager.ignored_coalesce_seconds = 0.01
    store = _FakeStore(record)
    manager.store = store
    broadcasts: list[str] = []

    def broadcast(event: str, _record: Any, _record_id: str) -> None:
        broadcasts.append(event)

    manager._broadcast = broadcast
    return manager, store, broadcasts


@pytest.mark.asyncio
async def test_repeated_identical_ignored_reason_is_coalesced() -> None:
    record = SimpleNamespace(id="one", revision=1, last_ignored_reason=None)
    manager, store, broadcasts = _manager(record)

    await manager._async_persist_ignored(record, "Cooldown is still active")
    assert store.save_count == 1
    assert broadcasts == ["ignored"]

    await manager._async_persist_ignored(record, "Cooldown is still active")
    await manager._async_persist_ignored(record, "Cooldown is still active")
    assert store.save_count == 1

    await asyncio.sleep(0.03)
    assert store.save_count == 2
    assert broadcasts == ["ignored", "ignored"]


@pytest.mark.asyncio
async def test_changed_ignored_reason_cancels_pending_repeat_and_persists_now() -> None:
    record = SimpleNamespace(id="one", revision=1, last_ignored_reason=None)
    manager, store, broadcasts = _manager(record)

    await manager._async_persist_ignored(record, "Ignored by debounce")
    await manager._async_persist_ignored(record, "Ignored by debounce")
    await manager._async_persist_ignored(record, "Outside the active period")

    assert store.save_count == 2
    assert broadcasts == ["ignored", "ignored"]
    await asyncio.sleep(0.03)
    assert store.save_count == 2
