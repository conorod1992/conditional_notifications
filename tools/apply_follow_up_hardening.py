from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}\n--- old ---\n{old}")
    file.write_text(text.replace(old, new, 1))


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} matches, found {count}")
    file.write_text(text.replace(old, new))


def append_once(path: str, marker: str, addition: str) -> None:
    file = Path(path)
    text = file.read_text()
    if marker in text:
        raise RuntimeError(f"{path}: marker already present: {marker}")
    file.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n")


# 1, 3, 4: strict event/template/local-time validation.
validation = "custom_components/conditional_notifications/validation.py"
replace_once(
    validation,
    '''def _strict_bool(data: dict[str, Any], key: str, path: str, *, default: bool | None = None) -> None:\n    if key in data:\n        if not isinstance(data[key], bool):\n            _error(path, "must be true or false")\n    elif default is not None:\n        data[key] = default\n\n\ndef _duration(data: dict[str, Any], key: str) -> None:\n''',
    '''def _strict_bool(data: dict[str, Any], key: str, path: str, *, default: bool | None = None) -> None:\n    if key in data:\n        if not isinstance(data[key], bool):\n            _error(path, "must be true or false")\n    elif default is not None:\n        data[key] = default\n\n\ndef _bounded_string(value: Any, path: str, label: str, *, maximum: int = 255) -> str:\n    if not isinstance(value, str):\n        _error(path, f"{label} must be a string")\n    normalized = value.strip()\n    if not normalized:\n        _error(path, "is required")\n    if len(normalized) > maximum:\n        _error(path, f"must be {maximum} characters or fewer")\n    return normalized\n\n\ndef _local_time(value: Any, path: str) -> time:\n    if not isinstance(value, str):\n        _error(path, "must use HH:MM or HH:MM:SS")\n    try:\n        parsed = time.fromisoformat(value)\n    except ValueError:\n        _error(path, "must use HH:MM or HH:MM:SS")\n    if parsed.tzinfo is not None:\n        _error(path, "must be a local time without a timezone offset")\n    return parsed\n\n\ndef _duration(data: dict[str, Any], key: str) -> None:\n''',
)
replace_once(
    validation,
    '''    elif kind == "event":\n        if not result.get("event_type"):\n            _error(f"{path}.event_type", "is required")\n        if not isinstance(result.get("event_data", {}), dict):\n            _error(f"{path}.event_data", "must be an object")\n    elif kind == "named" and not result.get("trigger_id"):\n        _error(f"{path}.trigger_id", "is required")\n''',
    '''    elif kind == "event":\n        event_type = _bounded_string(\n            result.get("event_type"), f"{path}.event_type", "event type"\n        )\n        if event_type in {"*", "state_reported"}:\n            _error(\n                f"{path}.event_type",\n                "uses a Home Assistant reserved event type that cannot be watched here",\n            )\n        result["event_type"] = event_type\n        if not isinstance(result.get("event_data", {}), dict):\n            _error(f"{path}.event_data", "must be an object")\n    elif kind == "named":\n        result["trigger_id"] = _bounded_string(\n            result.get("trigger_id"), f"{path}.trigger_id", "trigger name"\n        )\n''',
)
replace_once(
    validation,
    '''    if kind == "time":\n        try:\n            if "after" in result:\n                time.fromisoformat(result["after"])\n            if "before" in result:\n                time.fromisoformat(result["before"])\n        except (TypeError, ValueError):\n            _error(path, "time values must use HH:MM or HH:MM:SS")\n        if "after" not in result and "before" not in result:\n''',
    '''    if kind == "time":\n        if "after" in result:\n            _local_time(result["after"], f"{path}.after")\n        if "before" in result:\n            _local_time(result["before"], f"{path}.before")\n        if "after" not in result and "before" not in result:\n''',
)
replace_once(
    validation,
    '''        try:\n            time.fromisoformat(window["start"])\n            time.fromisoformat(window["end"])\n        except (KeyError, TypeError, ValueError):\n            _error("active_window", "start and end must be valid local times")\n''',
    '''        try:\n            _local_time(window["start"], "active_window.start")\n            _local_time(window["end"], "active_window.end")\n        except KeyError:\n            _error("active_window", "start and end must be valid local times")\n''',
)
replace_once(
    validation,
    '''    for key in ("title", "message"):\n        if not partial and not str(result.get(key, "")).strip():\n            _error(key, "is required")\n        if key in result and len(str(result[key])) > (255 if key == "title" else 4000):\n            _error(key, "is too long")\n''',
    '''    text_limits = {\n        "title": 255,\n        "message": 4000,\n        "expiry_title": 255,\n        "expiry_message": 4000,\n        "resolved_title": 255,\n        "resolved_message": 4000,\n    }\n    for key, maximum in text_limits.items():\n        if key in result:\n            if not isinstance(result[key], str):\n                _error(key, "must be a string")\n            if len(result[key]) > maximum:\n                _error(key, "is too long")\n    for key in ("title", "message"):\n        if not partial and not result.get(key, "").strip():\n            _error(key, "is required")\n''',
)

# 1 and 3: make startup/create defensive even if future listener/template validation regresses.
manager = "custom_components/conditional_notifications/manager.py"
replace_once(
    manager,
    '''        for record in list(self.store.records.values()):\n            try:\n                normalized = validate_definition(record.definition)\n                self._validate_templates(normalized)\n            except (DefinitionError, KeyError, TypeError, ValueError) as err:\n                self.store.records.pop(record.id, None)\n                self.store.invalid_records.append(record.as_dict())\n                quarantined = True\n                _LOGGER.warning(\n                    "Ignoring invalid persisted Conditional Notifications record %s: %s",\n                    record.id,\n                    err,\n                )\n                continue\n            normalized.pop("enabled", None)\n            record.definition = normalized\n            await self.async_rebuild(record, prove_current_durations=True)\n''',
    '''        for record in list(self.store.records.values()):\n            try:\n                normalized = validate_definition(record.definition)\n                self._validate_templates(normalized)\n                normalized.pop("enabled", None)\n                record.definition = normalized\n                await self.async_rebuild(record, prove_current_durations=True)\n            except (\n                DefinitionError,\n                HomeAssistantError,\n                KeyError,\n                TypeError,\n                ValueError,\n            ) as err:\n                if runtime := self._runtimes.pop(record.id, None):\n                    runtime.cancel()\n                self.store.records.pop(record.id, None)\n                self.store.invalid_records.append(record.as_dict())\n                quarantined = True\n                _LOGGER.warning(\n                    "Ignoring invalid persisted Conditional Notifications record %s: %s",\n                    record.id,\n                    err,\n                )\n                continue\n''',
)
replace_once(
    manager,
    '''            if source := definition.get(field):\n                try:\n                    Template(str(source), self.hass).ensure_valid()\n                except TemplateError as err:\n                    raise DefinitionError(field, str(err)) from err\n''',
    '''            if source := definition.get(field):\n                try:\n                    Template(source, self.hass).ensure_valid()\n                except (TemplateError, TypeError) as err:\n                    raise DefinitionError(field, str(err)) from err\n''',
)
replace_once(
    manager,
    '''        await self.store.async_save()\n        await self.async_rebuild(record, allow_current=True)\n        self._event("created", record)\n''',
    '''        await self.store.async_save()\n        try:\n            await self.async_rebuild(record, allow_current=True)\n        except Exception:\n            if runtime := self._runtimes.pop(record.id, None):\n                runtime.cancel()\n            self.store.records.pop(record.id, None)\n            self.store.history = [\n                item\n                for item in self.store.history\n                if not (item.notification_id == record.id and item.event == "created")\n            ]\n            await self.store.async_save()\n            raise\n        self._event("created", record)\n''',
)
replace_count(
    manager,
    'except (TemplateError, ValueError) as err:',
    'except (TemplateError, TypeError, ValueError) as err:',
    3,
)

# 2: preserve record-owner context for entity services and deny unscoped legacy services to non-admin owners.
delivery = "custom_components/conditional_notifications/delivery.py"
replace_once(
    delivery,
    '''from homeassistant.core import HomeAssistant\n''',
    '''from homeassistant.core import Context, HomeAssistant\n''',
)
replace_once(
    delivery,
    '''async def _async_service_call(\n    hass: HomeAssistant,\n    domain: str,\n    service: str,\n    data: dict[str, Any],\n    *,\n    target: dict[str, Any] | None = None,\n) -> None:\n    """Bound one provider call so a stuck target cannot wedge a delivery forever."""\n    async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):\n        if target is None:\n            await hass.services.async_call(domain, service, data, blocking=True)\n        else:\n            await hass.services.async_call(\n                domain,\n                service,\n                data,\n                blocking=True,\n                target=target,\n            )\n''',
    '''async def _async_service_call(\n    hass: HomeAssistant,\n    domain: str,\n    service: str,\n    data: dict[str, Any],\n    *,\n    target: dict[str, Any] | None = None,\n    context: Context | None = None,\n) -> None:\n    """Bound one provider call so a stuck target cannot wedge a delivery forever."""\n    kwargs: dict[str, Any] = {"blocking": True}\n    if target is not None:\n        kwargs["target"] = target\n    if context is not None:\n        kwargs["context"] = context\n    async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):\n        await hass.services.async_call(domain, service, data, **kwargs)\n\n\nasync def _delivery_identity(\n    hass: HomeAssistant, record: NotificationRecord\n) -> tuple[Context | None, bool]:\n    owner_id = getattr(record, "owner_id", None)\n    if owner_id is None:\n        return None, True\n    user = await hass.auth.async_get_user(owner_id)\n    return Context(user_id=owner_id), bool(user and user.is_admin)\n''',
)
replace_once(
    delivery,
    '''    delivery = merge_delivery(defaults, record.definition.get("delivery", {}))\n    entity_payload = {"title": title, "message": message}\n''',
    '''    delivery = merge_delivery(defaults, record.definition.get("delivery", {}))\n    context, owner_is_admin = await _delivery_identity(hass, record)\n    entity_payload = {"title": title, "message": message}\n''',
)
replace_once(
    delivery,
    '''                entity_payload,\n                target={"entity_id": entity_id},\n            )\n''',
    '''                entity_payload,\n                target={"entity_id": entity_id},\n                context=context,\n            )\n''',
)
replace_once(
    delivery,
    '''                {"message": message},\n                target={"entity_id": entity_id},\n            )\n''',
    '''                {"message": message},\n                target={"entity_id": entity_id},\n                context=context,\n            )\n''',
)
replace_once(
    delivery,
    '''    for service in delivery.get("notify_services", []):\n        try:\n            domain, service_name = service.split(".", 1) if "." in service else ("notify", service)\n''',
    '''    for service in delivery.get("notify_services", []):\n        if context is not None and not owner_is_admin:\n            results.append(\n                {\n                    "channel": service,\n                    "success": False,\n                    "error": "Legacy notify services require an administrator-owned notification",\n                }\n            )\n            continue\n        try:\n            domain, service_name = service.split(".", 1) if "." in service else ("notify", service)\n''',
)
replace_once(
    delivery,
    '''                service_payload,\n            )\n''',
    '''                service_payload,\n                context=context,\n            )\n''',
)

# 5: reject malformed history timestamps during load before pruning can encounter them.
storage = "custom_components/conditional_notifications/storage.py"
replace_once(
    storage,
    '''                if not isinstance(item, dict):\n                    raise ValueError("history item must be an object")\n                self.history.append(HistoryItem(**item))\n''',
    '''                if not isinstance(item, dict):\n                    raise ValueError("history item must be an object")\n                history_item = HistoryItem(**item)\n                if parse_datetime(history_item.timestamp) is None:\n                    raise ValueError("history timestamp is required")\n                self.history.append(history_item)\n''',
)

# 6: records is the canonical full list; searching is already performed client-side.
lifecycle = "custom_components/conditional_notifications/frontend/conditional-notifications-panel-lifecycle.js"
replace_once(
    lifecycle,
    '''        hass.callWS({type:`${WS}/list`, query:this.search || undefined}),\n''',
    '''        hass.callWS({type:`${WS}/list`}),\n''',
)

# Bust cached panel modules after the lifecycle fix.
replace_once(
    "custom_components/conditional_notifications/__init__.py",
    '_PANEL_ASSET_REVISION = "robustness1"',
    '_PANEL_ASSET_REVISION = "robustness2"',
)

# Python regressions covering all backend findings.
Path("tests/test_follow_up_hardening.py").write_text(
    '''"""Regression coverage for post-sweep validation, authorization, and storage gaps."""\n\nfrom __future__ import annotations\n\nfrom types import SimpleNamespace\nfrom unittest.mock import AsyncMock\n\nimport pytest\nfrom custom_components.conditional_notifications.const import DEFAULT_OPTIONS\nfrom custom_components.conditional_notifications.delivery import async_deliver\nfrom custom_components.conditional_notifications.manager import NotificationManager\nfrom custom_components.conditional_notifications.models import NotificationRecord\nfrom custom_components.conditional_notifications.storage import NotificationStore\nfrom custom_components.conditional_notifications.validation import (\n    DefinitionError,\n    validate_definition,\n)\n\n\ndef definition(**extra):\n    data = {\n        "name": "Watch",\n        "triggers": [\n            {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}\n        ],\n        "conditions": [],\n        "title": "Watch",\n        "message": "Matched",\n        "repeat_policy": "every",\n        "delivery": {"use_defaults": True},\n    }\n    data.update(extra)\n    return data\n\n\n@pytest.mark.parametrize("event_type", [["bad"], {"bad": True}, "*", "state_reported"])\ndef test_event_trigger_rejects_unusable_listener_types(event_type):\n    with pytest.raises(DefinitionError):\n        validate_definition(\n            definition(triggers=[{"type": "event", "event_type": event_type}])\n        )\n\n\ndef test_named_trigger_requires_a_string_identifier():\n    with pytest.raises(DefinitionError):\n        validate_definition(\n            definition(triggers=[{"type": "named", "trigger_id": ["bad"]}])\n        )\n\n\n@pytest.mark.parametrize(\n    ("field", "value"),\n    [\n        ("title", 123),\n        ("message", ["bad"]),\n        ("expiry_title", {"bad": True}),\n        ("expiry_message", 123),\n        ("resolved_title", ["bad"]),\n        ("resolved_message", {"bad": True}),\n    ],\n)\ndef test_notification_template_fields_must_be_strings(field, value):\n    with pytest.raises(DefinitionError, match="string"):\n        validate_definition(definition(**{field: value}))\n\n\ndef test_local_time_conditions_reject_timezone_offsets():\n    with pytest.raises(DefinitionError, match="timezone offset"):\n        validate_definition(\n            definition(conditions=[{"type": "time", "after": "09:00+00:00"}])\n        )\n\n\ndef test_recurring_windows_reject_timezone_offsets():\n    with pytest.raises(DefinitionError, match="timezone offset"):\n        validate_definition(\n            definition(active_window={"start": "09:00+00:00", "end": "17:00"})\n        )\n\n\n@pytest.mark.asyncio\nasync def test_user_owned_delivery_preserves_context_and_blocks_legacy_services():\n    async_call = AsyncMock()\n    hass = SimpleNamespace(\n        auth=SimpleNamespace(\n            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False))\n        ),\n        services=SimpleNamespace(async_call=async_call),\n    )\n    record = SimpleNamespace(\n        id="record-id",\n        owner_id="user-1",\n        definition={\n            "delivery": {\n                "use_defaults": False,\n                "persistent_notification": False,\n                "notify_entities": ["notify.phone"],\n                "notify_services": ["notify.mobile_app_phone"],\n            }\n        },\n    )\n\n    results = await async_deliver(hass, record, "Door", "Opened", {})\n\n    async_call.assert_awaited_once()\n    call = async_call.await_args\n    assert call.args[:3] == (\n        "notify",\n        "send_message",\n        {"title": "Door", "message": "Opened"},\n    )\n    assert call.kwargs["blocking"] is True\n    assert call.kwargs["target"] == {"entity_id": "notify.phone"}\n    assert call.kwargs["context"].user_id == "user-1"\n    assert results == [\n        {"channel": "notify.phone", "success": True},\n        {\n            "channel": "notify.mobile_app_phone",\n            "success": False,\n            "error": "Legacy notify services require an administrator-owned notification",\n        },\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_admin_owned_legacy_delivery_keeps_user_context():\n    async_call = AsyncMock()\n    hass = SimpleNamespace(\n        auth=SimpleNamespace(\n            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))\n        ),\n        services=SimpleNamespace(async_call=async_call),\n    )\n    record = SimpleNamespace(\n        id="record-id",\n        owner_id="admin-1",\n        definition={\n            "delivery": {\n                "use_defaults": False,\n                "persistent_notification": False,\n                "notify_services": ["notify.mobile_app_phone"],\n            }\n        },\n    )\n\n    results = await async_deliver(hass, record, "Door", "Opened", {})\n\n    async_call.assert_awaited_once()\n    assert async_call.await_args.kwargs["context"].user_id == "admin-1"\n    assert results == [{"channel": "notify.mobile_app_phone", "success": True}]\n\n\n@pytest.mark.asyncio\nasync def test_malformed_history_timestamp_is_dropped_during_load():\n    backing = SimpleNamespace(\n        async_load=AsyncMock(\n            return_value={\n                "records": [],\n                "history": [\n                    {\n                        "id": "bad",\n                        "notification_id": "n1",\n                        "timestamp": "not-a-timestamp",\n                        "event": "created",\n                        "summary": "Bad",\n                        "details": {},\n                        "owner_id": None,\n                    },\n                    {\n                        "id": "good",\n                        "notification_id": "n1",\n                        "timestamp": "2026-08-30T18:00:00+00:00",\n                        "event": "created",\n                        "summary": "Good",\n                        "details": {},\n                        "owner_id": None,\n                    },\n                ],\n            }\n        )\n    )\n    store = object.__new__(NotificationStore)\n    store._store = backing\n\n    await store.async_load()\n\n    assert [item.id for item in store.history] == ["good"]\n\n\nclass FakeStore:\n    def __init__(self, record):\n        self.records = {record.id: record}\n        self.invalid_records = []\n        self.history = []\n        self.saved = 0\n\n    async def async_load(self):\n        return None\n\n    async def async_save(self):\n        self.saved += 1\n\n    def add_history(self, item, **kwargs):\n        self.history.append(item)\n\n\n@pytest.mark.asyncio\nasync def test_persisted_bad_event_type_is_quarantined_without_rebuild():\n    bad_definition = definition(\n        triggers=[{"type": "event", "event_type": ["bad"]}]\n    )\n    record = NotificationRecord.create(bad_definition, "user-1")\n    manager = object.__new__(NotificationManager)\n    manager.options = dict(DEFAULT_OPTIONS)\n    manager.store = FakeStore(record)\n    manager._runtimes = {}\n    manager._locks = {}\n    manager._subscribers = set()\n    manager._tasks = set()\n    manager._shutting_down = False\n    manager._inflight_deliveries = set()\n    manager._delivery_tasks = {}\n    manager._pending_resolutions = {}\n\n    await manager.async_initialize()\n\n    assert record.id not in manager.store.records\n    assert manager.store.invalid_records[0]["id"] == record.id\n    assert manager.store.saved == 1\n'''
)

# Frontend regression: refresh must never replace the canonical list with server-filtered results.
append_once(
    "frontend/tests/loading-lifecycle.test.mjs",
    'test("refresh keeps the canonical record list complete while searching"',
    '''test("refresh keeps the canonical record list complete while searching", async () => {\n  const calls = [];\n  const currentConnection = connection();\n  const context = contextWith({\n    loaded:true,\n    search:"door",\n    hass:{\n      connection:currentConnection,\n      callWS: async (payload) => {\n        calls.push(payload);\n        return payload.type.endsWith("/list")\n          ? [{id:"door"},{id:"window"}]\n          : [];\n      },\n    },\n  });\n\n  await context.refresh();\n\n  assert.deepEqual(calls[0], {type:"conditional_notifications/list"});\n  assert.deepEqual(context.records, [{id:"door"},{id:"window"}]);\n});''',
)
