"""Regression coverage for native lifecycle, authorization, and recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.conditional_notifications.const import DEFAULT_OPTIONS
from custom_components.conditional_notifications.delivery import async_render
from custom_components.conditional_notifications.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.conditional_notifications.models import HistoryItem, NotificationRecord
from custom_components.conditional_notifications.native_automation import legacy_trigger_view
from custom_components.conditional_notifications.native_manager import LifecycleNotificationManager
from custom_components.conditional_notifications.native_security import (
    async_validate_native_observation_access,
)
from custom_components.conditional_notifications.validation import DefinitionError
from homeassistant import loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import condition as condition_helper
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import trigger as trigger_helper
from homeassistant.helpers.template import TemplateError
from homeassistant.util import dt as dt_util


@pytest.fixture
async def native_manager(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    await trigger_helper.async_setup(hass)
    await condition_helper.async_setup(hass)
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    manager = LifecycleNotificationManager(hass, {})
    hass.data["conditional_notifications"] = {"manager": manager}
    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver",
        AsyncMock(return_value=[{"channel": "test", "success": True}]),
    )
    yield manager
    await manager.async_shutdown()
    await hass.async_stop(force=True)


def definition(trigger=None, **changes):
    return {
        "name": "Review regression",
        "title": "Test",
        "message": "Matched",
        "triggers": [
            trigger or {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}
        ],
        "repeat_policy": "every",
        **changes,
    }


@pytest.mark.parametrize("duration", ["00:05:00", {"minutes": 5}, 300])
async def test_restart_keeps_valid_native_durations(native_manager, duration):
    manager = native_manager
    record = NotificationRecord.create(
        definition(
            {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on", "for": duration}
        ),
        None,
    )
    manager.store.records[record.id] = record
    manager.store.async_load = AsyncMock()
    await manager.async_initialize()
    assert record.id in manager.store.records
    assert not manager.store.invalid_records
    assert record.id in manager._runtimes


def test_projection_leaves_dynamic_and_disabled_triggers_to_ha():
    state = {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}
    assert legacy_trigger_view({**state, "for": "00:05:00"})["for"] == 300
    assert legacy_trigger_view({**state, "for": {"seconds": "{{ 5 }}"}}) is None
    assert legacy_trigger_view({**state, "enabled": False}) is None


@pytest.mark.parametrize("kind", ["state", "numeric_state"])
@pytest.mark.parametrize("resolution", [False, True])
async def test_seeded_native_duration_cancels_on_interruption(
    native_manager, monkeypatch, kind, resolution
):
    manager = native_manager
    hass = manager.hass
    entity = "binary_sensor.door" if kind == "state" else "sensor.temperature"
    match, mismatch = ("on", "off") if kind == "state" else ("15", "0")
    trigger = {
        "trigger": kind,
        "entity_id": entity,
        "for": "00:05:00",
        **({"to": "on"} if kind == "state" else {"above": 10}),
    }
    hass.states.async_set(entity, match)
    timers = []

    def timer(_hass, seconds, callback):
        item = {"cancelled": False, "callback": callback, "seconds": seconds}
        timers.append(item)
        return lambda: item.update(cancelled=True)

    monkeypatch.setattr(
        "custom_components.conditional_notifications.triggers.async_call_later", timer
    )
    record = NotificationRecord.create(
        definition({"type": "named", "trigger_id": "start"}, resolve_when=trigger)
        if resolution
        else definition(trigger),
        None,
    )
    record.active_occurrence = resolution
    manager.store.records[record.id] = record
    await manager.async_rebuild(record, prove_current_durations=True)
    assert timers and timers[0]["seconds"] == 300
    hass.states.async_set(entity, mismatch)
    await hass.async_block_till_done()
    hass.states.async_set(entity, match)
    await hass.async_block_till_done()
    assert timers[0]["cancelled"]
    assert record.notification_count == 0
    assert record.active_occurrence is resolution


async def test_overlapping_rebuilds_cancel_late_native_attachments(native_manager, monkeypatch):
    manager = native_manager
    record = NotificationRecord.create(definition(), None)
    manager.store.records[record.id] = record
    started, release = asyncio.Event(), asyncio.Event()
    subscriptions = []

    async def attach(runtime, _definition, _index, accepted, _name):
        first = not started.is_set()
        if first:
            started.set()
            await release.wait()
        item = {"active": True, "accepted": accepted}
        subscriptions.append(item)
        runtime.add(lambda: item.update(active=False))

    monkeypatch.setattr(
        "custom_components.conditional_notifications.native_manager.async_attach_native_trigger",
        attach,
    )
    manager._async_trigger = AsyncMock()
    first = asyncio.create_task(manager.async_rebuild(record))
    await started.wait()
    await manager.async_rebuild(record)
    release.set()
    await first
    assert sum(item["active"] for item in subscriptions) == 1
    for item in subscriptions:
        item["accepted"]({"type": "state"})
    await manager.hass.async_block_till_done()
    manager._async_trigger.assert_awaited_once()
    manager._runtimes[record.id].cancel()
    assert not any(item["active"] for item in subscriptions)


def restricted_user(hass, *, admin=False):
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(
            return_value=SimpleNamespace(
                is_admin=admin,
                is_active=True,
                permissions=SimpleNamespace(
                    access_all_entities=lambda _: False,
                    check_entity=lambda entity_id, _: entity_id == "binary_sensor.allowed",
                ),
            )
        )
    )


async def test_restricted_test_cannot_render_private_ha_state(native_manager):
    manager = native_manager
    restricted_user(manager.hass)
    manager.hass.states.async_set("sensor.private", "SECRET")
    data = definition(
        {"type": "named", "trigger_id": "test"}, message="{{ states('sensor.private') }}"
    )
    created = await manager.async_create(data, "restricted")
    record = manager.store.records[created["id"]]
    with pytest.raises(TemplateError):
        await manager.async_test(record)
    assert (
        await async_render(
            manager.hass, "{{ trigger.friendly_name }}", {"friendly_name": "Allowed"}, record
        )
        == "Allowed"
    )
    assert await async_render(manager.hass, "{{ now().strftime('%Y') }}", {}, record) == str(
        dt_util.now().year
    )
    restricted_user(manager.hass, admin=True)
    assert await async_render(manager.hass, data["message"], {}, record) == "SECRET"


@pytest.mark.parametrize(
    "field,value",
    [
        ("for", {"seconds": "{{ states('sensor.private') }}"}),
        ("variables", {"private": "{{ states('sensor.private') }}"}),
        ("enabled", "{{ is_state('sensor.private','on') }}"),
    ],
)
@pytest.mark.parametrize("section", ["triggers", "resolve_when"])
async def test_non_admin_observer_templates_are_rejected_recursively(
    native_manager, field, value, section
):
    restricted_user(native_manager.hass)
    trigger = {"trigger": "state", "entity_id": "binary_sensor.allowed", "to": "on", field: value}
    data = (
        definition(trigger)
        if section == "triggers"
        else definition({"type": "named", "trigger_id": "test"}, resolve_when=trigger)
    )
    with pytest.raises(DefinitionError, match="templated observers"):
        await async_validate_native_observation_access(native_manager.hass, data, "restricted")


@pytest.mark.parametrize("section", ["triggers", "conditions", "resolve_when"])
async def test_native_registry_uuid_checks_resolved_entity_permissions(native_manager, section):
    hass = native_manager.hass
    restricted_user(hass)
    private = er.async_get(hass).async_get_or_create(
        "binary_sensor", "demo", "private", suggested_object_id="private"
    )
    fragment = (
        {"condition": "state", "entity_id": private.id, "state": "on"}
        if section == "conditions"
        else {"trigger": "state", "entity_id": private.id, "to": "on"}
    )
    data = definition({"type": "named", "trigger_id": "test"})
    data[section] = fragment if section == "resolve_when" else [fragment]
    with pytest.raises(DefinitionError, match="not readable"):
        await async_validate_native_observation_access(hass, data, "restricted")


async def test_native_diagnostics_and_idle_history_retention(native_manager):
    manager = native_manager
    created = await manager.async_create(definition(), None)
    record = manager.store.records[created["id"]]
    old = HistoryItem.create(record.id, "created", "Old")
    old.timestamp = (dt_util.utcnow() - timedelta(days=40)).isoformat()
    manager.store.history.append(old)
    await manager._async_prune_retained_history()
    assert old not in manager.store.history
    result = await async_get_config_entry_diagnostics(
        manager.hass, SimpleNamespace(runtime_data=manager)
    )
    assert result["trigger_type_counts"] == {"state": 1}
    assert result["quarantined_count"] == 0
    assert result["options"]["history_retention_days"] == DEFAULT_OPTIONS["history_retention_days"]


async def test_quarantine_recovery_preserves_owner_and_id(native_manager):
    manager = native_manager
    restricted_user(manager.hass, admin=True)
    item = NotificationRecord.create(definition(), "original-owner").as_dict()
    manager.store.invalid_records.append(item)
    manager.store.invalid_reasons[item["id"]] = "Validation failed at triggers.0"
    assert not manager.quarantined_for_user("someone-else", False)
    assert (
        manager.quarantined_for_user("original-owner", False)[0]["reason"]
        == "Validation failed at triggers.0"
    )
    from custom_components.conditional_notifications.manager import NotFound

    with pytest.raises(NotFound):
        await manager.async_recover(item["id"], definition(), "someone-else", False)
    result = await manager.async_recover(item["id"], definition(), "administrator", True)
    assert result["id"] == item["id"]
    assert manager.store.records[item["id"]].owner_id == "original-owner"
    assert not manager.store.invalid_records


async def test_native_event_literal_filters_are_not_mistaken_for_entity_observers(native_manager):
    restricted_user(native_manager.hass)
    from homeassistant.auth.permissions.events import SUBSCRIBE_ALLOWLIST

    event_type = next(iter(SUBSCRIBE_ALLOWLIST - {"state_changed"}))
    data = definition(
        {
            "trigger": "event",
            "event_type": event_type,
            "event_data": {"zone": "literal", "entity_id": "literal"},
        }
    )
    await async_validate_native_observation_access(native_manager.hass, data, "restricted")


async def test_native_transition_evaluates_real_ha_conditions(native_manager):
    manager = native_manager
    hass = manager.hass
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("binary_sensor.allowed", "off")
    created = await manager.async_create(
        definition(
            conditions=[{"condition": "state", "entity_id": "binary_sensor.allowed", "state": "on"}]
        ),
        None,
    )
    record = manager.store.records[created["id"]]
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert record.notification_count == 0
    assert record.last_ignored_reason == "A condition did not pass"
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("binary_sensor.allowed", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert record.notification_count == 1
    assert record.last_trigger["to_state"]["state"] == "on"


async def test_native_event_resolution_during_delivery_is_not_lost(native_manager, monkeypatch):
    manager = native_manager
    started, release = asyncio.Event(), asyncio.Event()

    async def delivery(*_args, **_kwargs):
        started.set()
        await release.wait()
        return [{"channel": "test", "success": True}]

    monkeypatch.setattr(
        "custom_components.conditional_notifications.manager.async_deliver", delivery
    )
    created = await manager.async_create(
        definition(
            {"trigger": "event", "event_type": "review_start"},
            resolve_when={"trigger": "event", "event_type": "review_clear"},
        ),
        None,
    )
    record = manager.store.records[created["id"]]
    manager.hass.bus.async_fire("review_start")
    await asyncio.wait_for(started.wait(), timeout=2)
    manager.hass.bus.async_fire("review_clear")
    for _ in range(10):
        await asyncio.sleep(0)
        if manager._pending_resolution_store():
            break
    assert manager._pending_resolution_store()
    release.set()
    await manager.hass.async_block_till_done()
    assert record.notification_count == 1
    assert not record.active_occurrence
    assert record.status == "watching"


async def test_native_numeric_resolution_rechecks_current_truth(native_manager):
    manager = native_manager
    manager.hass.states.async_set("sensor.temperature", "0")
    created = await manager.async_create(
        definition(
            {"trigger": "numeric_state", "entity_id": "sensor.temperature", "above": 10},
            resolve_when={
                "trigger": "numeric_state",
                "entity_id": "sensor.temperature",
                "below": 5,
            },
        ),
        None,
    )
    record = manager.store.records[created["id"]]
    manager.hass.states.async_set("sensor.temperature", "15")
    await manager.hass.async_block_till_done()
    assert record.active_occurrence
    assert record.notification_count == 1
    manager.hass.states.async_set("sensor.temperature", "0")
    await manager.hass.async_block_till_done()
    assert not record.active_occurrence


def test_llm_definition_schema_is_structured_and_serializable():
    from custom_components.conditional_notifications.llm import CreateTool
    from homeassistant.helpers import llm

    # HA 2026.10 replaced voluptuous_openapi with probatio's native codec.
    if serializer := getattr(llm, "convert", None):
        schema = serializer(CreateTool.parameters)
    else:
        import probatio

        schema = probatio.to_openapi(CreateTool.parameters)
    definition_schema = schema["properties"]["definition"]
    assert set(definition_schema["required"]) == {"name", "title", "message", "triggers"}
    assert definition_schema["properties"]["triggers"]["type"] == "array"


async def test_options_flow_lists_companion_services_and_preserves_legacy_defaults(monkeypatch):
    from custom_components.conditional_notifications.config_flow import (
        ConditionalNotificationsOptionsFlow,
    )
    from homeassistant.config_entries import OptionsFlow

    options = {
        **DEFAULT_OPTIONS,
        "delivery": {**DEFAULT_OPTIONS["delivery"], "notify_services": ["notify.old_group"]},
    }
    monkeypatch.setattr(
        OptionsFlow, "config_entry", property(lambda _: SimpleNamespace(options=options))
    )
    flow = ConditionalNotificationsOptionsFlow()
    flow.hass = SimpleNamespace(
        services=SimpleNamespace(
            async_services=lambda: {"notify": {"mobile_app_phone": {}, "send_message": {}}}
        )
    )
    result = await flow.async_step_init()
    fields = result["data_schema"].schema
    selector = next(value for key, value in fields.items() if key.schema == "notify_services")
    assert selector.config["options"] == ["notify.mobile_app_phone", "notify.old_group"]
    saved = await flow.async_step_init(
        {"persistent_notification": True, "notify_services": ["notify.mobile_app_phone"]}
    )
    assert saved["data"]["delivery"]["notify_services"] == ["notify.mobile_app_phone"]
