"""Final PR review regressions through the actual native HA runtime."""

# Imported pytest fixtures are intentionally named by test parameters.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.conditional_notifications.models import NotificationRecord
from custom_components.conditional_notifications.native_automation import (
    async_attach_native_resolution_trigger,
    async_attach_native_trigger,
)
from custom_components.conditional_notifications.triggers import RuntimeSubscriptions
from custom_components.conditional_notifications.validation import DefinitionError
from homeassistant.core import State

from test_review_regressions import definition, native_manager, restricted_user  # noqa: F401


@pytest.mark.parametrize("kind", ["state", "numeric_state"])
async def test_resolution_proof_cancels_even_if_a_bounce_is_queued_together(
    native_manager, monkeypatch, kind
):
    manager = native_manager
    entity = "binary_sensor.door" if kind == "state" else "sensor.temperature"
    matching, mismatch = ("on", "off") if kind == "state" else ("15", "0")
    resolution = {
        "trigger": kind,
        "entity_id": entity,
        "for": 30,
        **({"to": "on"} if kind == "state" else {"above": 10}),
    }
    manager.hass.states.async_set(entity, matching)
    timers = []

    def timer(_hass, _seconds, callback):
        item = {"cancelled": False, "callback": callback}
        timers.append(item)
        return lambda: item.update(cancelled=True)

    monkeypatch.setattr(
        "custom_components.conditional_notifications.triggers.async_call_later", timer
    )
    record = NotificationRecord.create(
        definition({"type": "named", "trigger_id": "start"}, resolve_when=resolution), None
    )
    record.active_occurrence = True
    manager.store.records[record.id] = record
    await manager.async_rebuild(record)
    manager.hass.states.async_set(entity, mismatch)
    manager.hass.states.async_set(entity, matching)
    await manager.hass.async_block_till_done()
    assert timers[0]["cancelled"], "Each event must be evaluated, not only the final HA state"
    assert record.active_occurrence


async def test_resolution_guard_uses_the_event_snapshot_not_the_latest_state(native_manager):
    manager = native_manager
    manager.hass.states.async_set("binary_sensor.door", "on")
    record = NotificationRecord.create(
        definition(
            resolve_when={
                "trigger": "state",
                "entity_id": "binary_sensor.door",
                "to": "on",
                "for": 30,
            }
        ),
        None,
    )
    record.active_occurrence = True
    runtime = RuntimeSubscriptions(manager.hass, record.id, record.revision)
    runtime.guard_duration = Mock()
    runtime.schedule_duration = Mock()
    manager._runtimes[record.id] = runtime
    await manager._async_resolve_if_current(record)
    predicate = runtime.guard_duration.call_args.args[2]
    assert predicate(State("binary_sensor.door", "off")) is False
    assert predicate(State("binary_sensor.door", "on")) is True


async def test_disabled_native_fragments_remain_valid_and_never_match_current(native_manager):
    manager = native_manager
    manager.hass.states.async_set("binary_sensor.door", "on")
    created = await manager.async_create(
        definition(
            {"trigger": "state", "entity_id": "binary_sensor.door", "to": "on", "enabled": False},
            match_current_state=True,
        ),
        None,
    )
    record = manager.store.records[created["id"]]
    assert record.notification_count == 0
    manager.hass.states.async_set("binary_sensor.door", "off")
    manager.hass.states.async_set("binary_sensor.door", "on")
    await manager.hass.async_block_till_done()
    assert record.notification_count == 0


@pytest.mark.parametrize(
    "trigger",
    [
        {
            "triggers": [
                {
                    "trigger": "state",
                    "entity_id": "binary_sensor.door",
                    "to": "on",
                    "enabled": False,
                }
            ]
        },
        {
            "trigger": "state",
            "entity_id": "binary_sensor.door",
            "to": "on",
            "enabled": "{{ false }}",
        },
    ],
)
async def test_disabled_native_group_is_not_quarantined(native_manager, trigger):
    created = await native_manager.async_create(definition(trigger), None)
    assert created["id"] in native_manager.store.records


@pytest.mark.parametrize("resolution", [False, True])
async def test_cancelled_native_attachment_disposes_a_late_result(
    native_manager, monkeypatch, resolution
):
    started, release = asyncio.Event(), asyncio.Event()
    active = []
    runtime = RuntimeSubscriptions(native_manager.hass, "test", 1)

    async def initialize(*_args, **_kwargs):
        active.append("listener")
        started.set()
        await release.wait()
        return lambda: active.clear()

    monkeypatch.setattr(
        "custom_components.conditional_notifications.native_automation.async_prepare_native_trigger",
        AsyncMock(return_value=[{"platform": "state"}]),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.native_automation.trigger_helper.async_initialize_triggers",
        initialize,
    )
    attach = async_attach_native_resolution_trigger if resolution else async_attach_native_trigger
    task = asyncio.create_task(attach(runtime, {"trigger": "state"}, 0, Mock(), "test"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await native_manager.hass.async_block_till_done()
    assert runtime.cancelled
    assert not active


async def test_failed_native_attachment_is_not_mistaken_for_a_disabled_group(
    native_manager, monkeypatch
):
    import logging

    async def initialize(_hass, _validated, _action, _domain, _name, log_callback):
        log_callback(logging.ERROR, "Failed to attach a trigger")
        return None

    monkeypatch.setattr(
        "custom_components.conditional_notifications.native_automation.async_prepare_native_trigger",
        AsyncMock(return_value=[{"platform": "state"}]),
    )
    monkeypatch.setattr(
        "custom_components.conditional_notifications.native_automation.trigger_helper.async_initialize_triggers",
        initialize,
    )
    runtime = RuntimeSubscriptions(native_manager.hass, "test", 1)
    with pytest.raises(DefinitionError, match="did not attach"):
        await async_attach_native_trigger(runtime, {"trigger": "state"}, 0, Mock(), "test")


@pytest.mark.parametrize("missing", ["unknown", "unavailable", None, "no_location"])
async def test_legacy_zone_leave_does_not_fire_when_location_is_lost(native_manager, missing):
    manager = native_manager
    manager.hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0, "radius": 100})
    manager.hass.states.async_set("person.test", "home", {"in_zones": ["zone.home"]})
    created = await manager.async_create(
        definition(
            {
                "type": "zone",
                "entity_id": "person.test",
                "zone_entity_id": "zone.home",
                "event": "leave",
            }
        ),
        None,
    )
    if missing is None:
        manager.hass.states.async_remove("person.test")
    else:
        manager.hass.states.async_set("person.test", missing)
    await manager.hass.async_block_till_done()
    assert manager.store.records[created["id"]].notification_count == 0
    manager.hass.states.async_set("person.test", "home", {"in_zones": ["zone.home"]})
    await manager.hass.async_block_till_done()
    manager.hass.states.async_set("person.test", "not_home", {"in_zones": []})
    await manager.hass.async_block_till_done()
    assert manager.store.records[created["id"]].notification_count == 1


@pytest.mark.parametrize("missing", ["unavailable", "missing_zone"])
async def test_zone_leave_resolution_cannot_treat_missing_data_as_outside(native_manager, missing):
    manager = native_manager
    manager.hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0, "radius": 100})
    manager.hass.states.async_set(
        "person.test", "unavailable" if missing == "unavailable" else "not_home", {"in_zones": []}
    )
    if missing == "missing_zone":
        manager.hass.states.async_remove("zone.home")
    record = NotificationRecord.create(
        definition(
            resolve_when={
                "trigger": "zone",
                "entity_id": "person.test",
                "zone": "zone.home",
                "event": "leave",
            }
        ),
        None,
    )
    record.active_occurrence = True
    assert manager._current_resolution_context(record) is None


async def test_revoked_entity_permissions_stop_before_recording_the_next_event(native_manager):
    manager = native_manager
    restricted_user(manager.hass)
    manager.hass.states.async_set("binary_sensor.allowed", "off")
    created = await manager.async_create(
        definition({"trigger": "state", "entity_id": "binary_sensor.allowed", "to": "on"}),
        "restricted",
    )
    record = manager.store.records[created["id"]]
    manager.hass.auth.async_get_user.return_value.permissions.check_entity = lambda *_: False
    manager.hass.states.async_set("binary_sensor.allowed", "on", {"private": "newly hidden"})
    await manager.hass.async_block_till_done()
    assert record.notification_count == 0
    assert record.last_trigger is None
    assert not any(item.event == "matched" for item in manager.store.history)
    assert record.status == "error"
    assert record.id not in manager._runtimes


@pytest.mark.parametrize("action", ["test", "resolve"])
async def test_revocation_also_blocks_tests_and_resolution_events(native_manager, action):
    manager = native_manager
    restricted_user(manager.hass)
    created = await manager.async_create(
        definition({"trigger": "state", "entity_id": "binary_sensor.allowed", "to": "on"}),
        "restricted",
    )
    record = manager.store.records[created["id"]]
    manager.hass.auth.async_get_user.return_value.permissions.check_entity = lambda *_: False
    if action == "test":
        with pytest.raises(DefinitionError, match="permission"):
            await manager.async_test(record)
    else:
        record.active_occurrence = True
        await manager._async_resolve(
            record.id, record.revision, {"private": "must not be recorded"}
        )
    assert record.status == "error"
    assert not any(item.event in {"matched", "resolved"} for item in manager.store.history)
    assert all(not item.details.get("trigger") for item in manager.store.history)


async def test_stale_permission_check_cannot_disable_a_newer_revision(native_manager, monkeypatch):
    manager = native_manager
    created = await manager.async_create(definition(), None)
    record = manager.store.records[created["id"]]
    started, release = asyncio.Event(), asyncio.Event()

    async def check(_record):
        started.set()
        await release.wait()
        raise DefinitionError("triggers.0.entity_id", "not readable")

    monkeypatch.setattr(manager, "_async_check_observation_access", check)
    task = asyncio.create_task(
        manager._async_trigger(record.id, record.revision, {"type": "manual"})
    )
    await started.wait()
    record.revision += 1
    manager._runtimes[record.id].cancel()
    runtime = RuntimeSubscriptions(manager.hass, record.id, record.revision)
    manager._runtimes[record.id] = runtime
    release.set()
    await task
    assert record.enabled and record.status == "watching"
    assert not runtime.cancelled


@pytest.mark.parametrize("reasons", [None, [], "invalid", {"record": {"unexpected": "object"}}])
async def test_malformed_quarantine_metadata_does_not_break_valid_watch_loading(
    native_manager, reasons
):
    store = native_manager.store
    record = NotificationRecord.create(definition(), None)
    store._store.async_load = AsyncMock(
        return_value={"records": [record.as_dict()], "invalid_reasons": reasons, "history": []}
    )
    await store.async_load()
    assert record.id in store.records
    assert store.invalid_reasons == {}
