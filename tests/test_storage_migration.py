"""Storage migration seam tests."""

from __future__ import annotations

import pytest
from custom_components.conditional_notifications.models import HistoryItem
from custom_components.conditional_notifications.storage import _VersionedStore


@pytest.mark.asyncio
async def test_v0_watches_migrate_to_records():
    store = object.__new__(_VersionedStore)
    result = await store._async_migrate_func(0, 1, {"watches": [{"id": "one"}], "history": []})
    assert result == {"records": [{"id": "one"}], "history": []}


@pytest.mark.asyncio
async def test_unknown_major_version_is_not_guessed():
    store = object.__new__(_VersionedStore)
    with pytest.raises(NotImplementedError):
        await store._async_migrate_func(99, 1, {})


def test_legacy_history_without_owner_metadata_still_loads():
    item = HistoryItem(
        id="history-one",
        notification_id="one",
        timestamp="2026-01-01T00:00:00+00:00",
        event="created",
        summary="Created",
    )
    assert item.owner_id is None
