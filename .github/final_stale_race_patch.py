from pathlib import Path

manager_path = Path("custom_components/conditional_notifications/manager.py")
text = manager_path.read_text()

replacements = [
    (
        '''        record = self.store.records.get(record_id)\n        if not record:\n            return\n        delivery_key: tuple[str, int, str] | None = None\n''',
        '''        delivery_key: tuple[str, int, str] | None = None\n''',
    ),
    (
        '''            async with self._lock(record_id):\n                if record.revision != revision or not record.enabled or record.paused:\n                    return\n''',
        '''            async with self._lock(record_id):\n                record = self.store.records.get(record_id)\n                if (\n                    record is None\n                    or record.revision != revision\n                    or not record.enabled\n                    or record.paused\n                ):\n                    return\n''',
    ),
    (
        '''        record = self.store.records.get(record_id)\n        if not record:\n            return\n        async with self._lock(record_id):\n            if record.revision != revision or not record.active_occurrence:\n                return\n''',
        '''        async with self._lock(record_id):\n            record = self.store.records.get(record_id)\n            if record is None or record.revision != revision or not record.active_occurrence:\n                return\n''',
    ),
    (
        '''        record = self.store.records.get(record_id)\n        if not record:\n            return\n        should_notify = False\n        async with self._lock(record_id):\n            if record.revision != revision or record.status == "expired":\n                return\n''',
        '''        should_notify = False\n        async with self._lock(record_id):\n            record = self.store.records.get(record_id)\n            if record is None or record.revision != revision or record.status == "expired":\n                return\n''',
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one manager replacement, found {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)

manager_path.write_text(text)

test_path = Path("tests/test_lifecycle_concurrency_hardening.py")
tests = test_path.read_text()
marker = '''\n\n@pytest.mark.asyncio\nasync def test_resolution_waits_for_initial_delivery_commit(manager, monkeypatch) -> None:\n'''
if tests.count(marker) != 1:
    raise RuntimeError("Could not locate concurrency test insertion point")

addition = '''\n\n@pytest.mark.asyncio\nasync def test_waiting_trigger_does_not_deliver_after_record_is_deleted(\n    manager, monkeypatch\n) -> None:\n    render = AsyncMock(return_value="rendered")\n    deliver = AsyncMock(return_value=[{"channel": "test", "success": True}])\n    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_render", render\n    )\n    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_deliver", deliver\n    )\n    record = NotificationRecord.create(definition(), "u1")\n    manager.store.records[record.id] = record\n    lock = manager._lock(record.id)\n    await lock.acquire()\n\n    task = asyncio.create_task(\n        manager._async_trigger(record.id, record.revision, {"type": "state"})\n    )\n    await asyncio.sleep(0)\n    manager.store.records.pop(record.id)\n    lock.release()\n    await task\n\n    render.assert_not_awaited()\n    deliver.assert_not_awaited()\n    assert manager.store.history == []\n\n\n@pytest.mark.asyncio\nasync def test_waiting_resolution_has_no_side_effect_after_record_is_deleted(\n    manager, monkeypatch\n) -> None:\n    clear = Mock()\n    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_clear", clear\n    )\n    record = NotificationRecord.create(definition(resolve=True), "u1")\n    record.active_occurrence = True\n    record.status = "active"\n    manager.store.records[record.id] = record\n    lock = manager._lock(record.id)\n    await lock.acquire()\n\n    task = asyncio.create_task(\n        manager._async_resolve(record.id, record.revision, {"type": "state"})\n    )\n    await asyncio.sleep(0)\n    manager.store.records.pop(record.id)\n    lock.release()\n    await task\n\n    clear.assert_not_called()\n    assert record.active_occurrence\n    assert record.status == "active"\n    assert manager.store.history == []\n\n\n@pytest.mark.asyncio\nasync def test_waiting_expiry_does_not_deliver_after_record_is_deleted(\n    manager, monkeypatch\n) -> None:\n    render = AsyncMock(return_value="rendered")\n    deliver = AsyncMock(return_value=[{"channel": "test", "success": True}])\n    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_render", render\n    )\n    monkeypatch.setattr(\n        "custom_components.conditional_notifications.manager.async_deliver", deliver\n    )\n    expiry_definition = definition()\n    expiry_definition["notify_on_expiry"] = True\n    record = NotificationRecord.create(expiry_definition, "u1")\n    manager.store.records[record.id] = record\n    lock = manager._lock(record.id)\n    await lock.acquire()\n\n    task = asyncio.create_task(manager._async_expire(record.id, record.revision))\n    await asyncio.sleep(0)\n    manager.store.records.pop(record.id)\n    lock.release()\n    await task\n\n    render.assert_not_awaited()\n    deliver.assert_not_awaited()\n    assert record.status == "watching"\n    assert manager.store.history == []\n'''

tests = tests.replace(marker, addition + marker, 1)
test_path.write_text(tests)
