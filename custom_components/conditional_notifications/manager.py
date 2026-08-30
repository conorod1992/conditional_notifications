"""Race-safe lifecycle manager for persistent event-driven notifications."""

from __future__ import annotations

import asyncio
import logging
import math
from copy import deepcopy
from datetime import timedelta
from typing import Any

from homeassistant.components.zone.condition import zone as zone_condition
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConditionError, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import dt as dt_util

from .conditions import (
    async_evaluate_conditions,
    is_unknown_state,
    numeric_matches,
    state_value,
)
from .const import DEFAULT_OPTIONS, DOMAIN, EVENTS, SIGNAL_CHANGED
from .delivery import async_clear, async_deliver, async_render
from .models import HistoryItem, NotificationRecord, duration_seconds, parse_datetime, utc_iso
from .storage import NotificationStore
from .triggers import RuntimeSubscriptions, attach_trigger
from .validation import DefinitionError, validate_definition

_LOGGER = logging.getLogger(__name__)


class NotFound(HomeAssistantError):
    """Requested record was not found."""


class AmbiguousReference(HomeAssistantError):
    """A safe mutating lookup found multiple candidates."""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        super().__init__("Reference is ambiguous")
        self.candidates = candidates


class RevisionConflict(HomeAssistantError):
    """A mutation was based on stale state or raced active delivery."""


class NotificationManager:
    """Own records, subscriptions, timers, history, and delivery transitions."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = {**DEFAULT_OPTIONS, **options}
        self.store = NotificationStore(hass)
        self._runtimes: dict[str, RuntimeSubscriptions] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: set[Any] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False
        self._inflight_deliveries: set[tuple[str, int, str]] = set()
        self._delivery_tasks: dict[tuple[str, int, str], asyncio.Task[Any]] = {}
        self._pending_resolutions: dict[tuple[str, int, str], dict[str, Any]] = {}

    def _task_store(self) -> set[asyncio.Task[Any]]:
        store = getattr(self, "_tasks", None)
        if store is None:
            store = set()
            self._tasks = store
        return store

    def _delivery_store(self) -> set[tuple[str, int, str]]:
        store = getattr(self, "_inflight_deliveries", None)
        if store is None:
            store = set()
            self._inflight_deliveries = store
        return store

    def _delivery_task_store(self) -> dict[tuple[str, int, str], asyncio.Task[Any]]:
        store = getattr(self, "_delivery_tasks", None)
        if store is None:
            store = {}
            self._delivery_tasks = store
        return store

    def _pending_resolution_store(
        self,
    ) -> dict[tuple[str, int, str], dict[str, Any]]:
        store = getattr(self, "_pending_resolutions", None)
        if store is None:
            store = {}
            self._pending_resolutions = store
        return store

    def _is_shutting_down(self) -> bool:
        return bool(getattr(self, "_shutting_down", False))

    def _schedule_task(self, coroutine: Any) -> asyncio.Task[Any] | None:
        """Create a task owned by this manager so unload can cancel it safely."""
        if self._is_shutting_down():
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()
            return None
        task = self.hass.async_create_task(coroutine, eager_start=True)
        self._task_store().add(task)
        task.add_done_callback(self._task_store().discard)
        return task

    def _require_current_record(self, record: NotificationRecord) -> NotificationRecord:
        current = self.store.records.get(record.id)
        if current is not record:
            raise NotFound(f"Conditional notification '{record.id}' no longer exists")
        return current

    @staticmethod
    def _require_expected_revision(
        record: NotificationRecord, expected_revision: int | None
    ) -> None:
        if expected_revision is not None and record.revision != expected_revision:
            raise RevisionConflict(
                "Conditional notification changed while it was being edited; "
                "reload it and try again"
            )

    def _ensure_not_delivering(self, record_id: str) -> None:
        if any(key[0] == record_id for key in self._delivery_store()):
            raise RevisionConflict(
                "Conditional notification is currently delivering; wait for delivery to "
                "finish and try again"
            )

    def _clear_delivery_state(self, record_id: str) -> list[asyncio.Task[Any]]:
        keys = [key for key in self._delivery_store() if key[0] == record_id]
        tasks: list[asyncio.Task[Any]] = []
        current_task = asyncio.current_task()
        for key in keys:
            self._delivery_store().discard(key)
            self._pending_resolution_store().pop(key, None)
            task = self._delivery_task_store().pop(key, None)
            if task is not None and task is not current_task:
                tasks.append(task)
        return tasks

    async def async_initialize(self) -> None:
        self._shutting_down = False
        await self.store.async_load()
        self._prune_history()
        quarantined = False
        for record in list(self.store.records.values()):
            try:
                normalized = validate_definition(record.definition)
                self._validate_templates(normalized)
                normalized.pop("enabled", None)
                record.definition = normalized
                await self.async_rebuild(record, prove_current_durations=True)
            except (
                DefinitionError,
                HomeAssistantError,
                KeyError,
                TypeError,
                ValueError,
            ) as err:
                if runtime := self._runtimes.pop(record.id, None):
                    runtime.cancel()
                self.store.records.pop(record.id, None)
                self.store.invalid_records.append(record.as_dict())
                quarantined = True
                _LOGGER.warning(
                    "Ignoring invalid persisted Conditional Notifications record %s: %s",
                    record.id,
                    err,
                )
                continue
        if quarantined:
            await self.store.async_save()

    async def async_shutdown(self) -> None:
        self._shutting_down = True
        for runtime in self._runtimes.values():
            runtime.cancel()
        self._runtimes.clear()

        tasks = list(self._task_store())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._task_store().clear()
        self._delivery_store().clear()
        self._delivery_task_store().clear()
        self._pending_resolution_store().clear()
        self._subscribers.clear()
        await self.store.async_save()

    @callback
    def subscribe(self, listener: Any) -> Any:
        self._subscribers.add(listener)

        @callback
        def unsubscribe() -> None:
            self._subscribers.discard(listener)

        return unsubscribe

    def _broadcast(self, event: str, record: NotificationRecord | None, record_id: str) -> None:
        if self._is_shutting_down():
            return
        payload = {
            "event": event,
            "notification_id": record_id,
            "record": record.public_dict(dt_util.now()) if record else None,
            "owner_id": record.owner_id if record else None,
        }
        listeners = set(self._subscribers)
        domain_data = self.hass.data.get(DOMAIN, {})
        listeners.update(domain_data.get("subscribers", ()))
        for listener in list(listeners):
            listener(payload)
        async_dispatcher_send(self.hass, SIGNAL_CHANGED)

    def broadcast_reload(self) -> None:
        """Prompt persistent WebSocket subscribers to refresh after manager replacement."""
        self._broadcast("reloaded", None, "")

    def _event(self, event: str, record: NotificationRecord) -> None:
        if self._is_shutting_down():
            return
        if event in EVENTS:
            self.hass.bus.async_fire(
                f"{DOMAIN}_{event}",
                {"notification_id": record.id, "name": record.name, "status": record.status},
            )
        self._broadcast(event, record, record.id)

    def _add_history(
        self,
        record: NotificationRecord,
        event: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self.options.get("retain_content", True) and details:
            details = {
                key: value for key, value in details.items() if key not in {"title", "message"}
            }
        self.store.add_history(
            HistoryItem.create(record.id, event, summary, details, record.owner_id),
            days=int(self.options["history_retention_days"]),
            maximum=int(self.options["history_max_records"]),
        )

    def _prune_history(self) -> None:
        if self.store.history:
            latest = self.store.history.pop()
            self.store.add_history(
                latest,
                days=int(self.options["history_retention_days"]),
                maximum=int(self.options["history_max_records"]),
            )

    def _validate_templates(self, definition: dict[str, Any]) -> None:
        for field in (
            "title",
            "message",
            "expiry_title",
            "expiry_message",
            "resolved_title",
            "resolved_message",
        ):
            if source := definition.get(field):
                try:
                    Template(source, self.hass).ensure_valid()
                except (TemplateError, TypeError) as err:
                    raise DefinitionError(field, str(err)) from err

    def _lock(self, record_id: str) -> asyncio.Lock:
        return self._locks.setdefault(record_id, asyncio.Lock())

    def can_access(self, record: NotificationRecord, user_id: str | None, is_admin: bool) -> bool:
        return is_admin or record.owner_id is None or record.owner_id == user_id

    def history_for_user(
        self, user_id: str | None, is_admin: bool, notification_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return retained history without losing ownership after deletion."""
        items = self.store.history_for(notification_id)
        if is_admin:
            return items
        accessible_records = {
            record.id
            for record in self.store.records.values()
            if self.can_access(record, user_id, False)
        }
        return [
            item
            for item in items
            if item["owner_id"] == user_id
            or (item["owner_id"] is None and item["notification_id"] in accessible_records)
        ]

    @staticmethod
    def _definition_is_semantic_change(
        old_definition: dict[str, Any], new_definition: dict[str, Any]
    ) -> bool:
        semantic_fields = {
            "triggers",
            "conditions",
            "repeat_policy",
            "max_notifications",
            "available_from",
            "expires_at",
            "active_window",
            "cooldown",
            "debounce",
            "notify_on_expiry",
            "resolve_when",
            "clear_on_resolve",
        }
        return any(old_definition.get(key) != new_definition.get(key) for key in semantic_fields)

    @staticmethod
    def _completed_naturally(record: NotificationRecord) -> bool:
        policy = record.definition.get("repeat_policy")
        return record.notification_count > 0 and (
            policy == "once"
            or (
                policy == "limited"
                and record.notification_count >= int(record.definition.get("max_notifications", 0))
            )
        )

    @staticmethod
    def _reset_runtime_state(record: NotificationRecord) -> None:
        record.notification_count = 0
        record.qualifying_match_seen = False
        record.last_accepted_at = None
        record.last_trigger_at = None
        record.last_trigger = None
        record.last_ignored_reason = None
        record.last_delivery = []
        record.active_occurrence = False

    def list_records(
        self, user_id: str | None, is_admin: bool, query: str | None = None
    ) -> list[dict[str, Any]]:
        records = [r for r in self.store.records.values() if self.can_access(r, user_id, is_admin)]
        if query:
            term = query.casefold()
            records = [
                r
                for r in records
                if term in r.name.casefold()
                or term in (r.description or "").casefold()
                or term in (r.semantic_key or "").casefold()
                or any(
                    term in str(t.get("entity_id", "")).casefold()
                    for t in r.definition.get("triggers", [])
                )
            ]
        return [
            record.public_dict(dt_util.now())
            for record in sorted(records, key=lambda r: r.updated_at, reverse=True)
        ]

    def resolve(
        self,
        reference: str,
        user_id: str | None,
        is_admin: bool,
        *,
        entity_hint: str | None = None,
    ) -> NotificationRecord:
        available = [
            r for r in self.store.records.values() if self.can_access(r, user_id, is_admin)
        ]
        exact = [r for r in available if r.id == reference]
        if not exact:
            exact = [r for r in available if r.semantic_key and r.semantic_key == reference]
        if not exact:
            exact = [r for r in available if r.name.casefold() == reference.casefold()]
        if not exact and entity_hint:
            exact = [
                r
                for r in available
                if any(t.get("entity_id") == entity_hint for t in r.definition.get("triggers", []))
            ]
        if not exact:
            raise NotFound(f"No conditional notification matches '{reference}'")
        if len(exact) > 1:
            raise AmbiguousReference(
                [{"id": r.id, "name": r.name, "status": r.status} for r in exact]
            )
        return exact[0]

    async def async_create(
        self, definition: dict[str, Any], owner_id: str | None
    ) -> dict[str, Any]:
        normalized = validate_definition(definition)
        self._validate_templates(normalized)
        record = NotificationRecord.create(normalized, owner_id)
        record.enabled = bool(normalized.pop("enabled", True))
        if not record.enabled:
            record.status = "disabled"
        self.store.records[record.id] = record
        self._add_history(record, "created", "Conditional notification created")
        await self.store.async_save()
        try:
            await self.async_rebuild(record, allow_current=True)
        except Exception:
            if runtime := self._runtimes.pop(record.id, None):
                runtime.cancel()
            self.store.records.pop(record.id, None)
            self.store.history = [
                item
                for item in self.store.history
                if not (item.notification_id == record.id and item.event == "created")
            ]
            await self.store.async_save()
            raise
        self._event("created", record)
        return record.public_dict(dt_util.now())

    async def async_update(
        self,
        record: NotificationRecord,
        changes: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock(record.id):
            self._require_current_record(record)
            self._require_expected_revision(record, expected_revision)
            self._ensure_not_delivering(record.id)
            merged = deepcopy(record.definition)
            merged.update(changes)
            merged["name"] = changes.get("name", record.name)
            normalized = validate_definition(merged)
            self._validate_templates(normalized)
            requested_enabled = bool(normalized.pop("enabled", record.enabled))
            semantic_change = self._definition_is_semantic_change(record.definition, normalized)
            naturally_completed = self._completed_naturally(record)
            previous_status = record.status
            abandoned_active_occurrence = semantic_change and record.active_occurrence
            record.revision += 1
            record.definition = normalized
            record.name = normalized["name"]
            record.semantic_key = normalized.get("semantic_key")
            record.description = normalized.get("description")
            if "enabled" in changes:
                record.enabled = requested_enabled
            elif semantic_change and (
                naturally_completed or previous_status in {"expired", "error"}
            ):
                record.enabled = True
            if semantic_change:
                self._reset_runtime_state(record)
            record.updated_at = utc_iso()
            if semantic_change or "enabled" in changes:
                record.status = (
                    "paused" if record.paused else ("watching" if record.enabled else "disabled")
                )
            self._add_history(
                record,
                "updated",
                "Definition updated; runtime progress reset"
                if semantic_change
                else "Definition updated",
                {"runtime_reset": semantic_change},
            )
            await self.store.async_save()
        if abandoned_active_occurrence:
            async_clear(self.hass, record.id)
        await self.async_rebuild(record)
        self._event("updated", record)
        return record.public_dict(dt_util.now())

    async def async_duplicate(
        self, record: NotificationRecord, owner_id: str | None, name: str | None = None
    ) -> dict[str, Any]:
        async with self._lock(record.id):
            self._require_current_record(record)
            definition = deepcopy(record.definition)
            definition["name"] = name or f"{record.name} copy"
            definition.pop("semantic_key", None)
        return await self.async_create(definition, owner_id)

    async def async_delete(self, record: NotificationRecord) -> dict[str, Any]:
        delivery_tasks: list[asyncio.Task[Any]] = []
        async with self._lock(record.id):
            self._require_current_record(record)
            if runtime := self._runtimes.pop(record.id, None):
                runtime.cancel()
            self.store.records.pop(record.id, None)
            delivery_tasks = self._clear_delivery_state(record.id)
            self._add_history(record, "deleted", "Conditional notification deleted")
            await self.store.async_save()
        for task in delivery_tasks:
            task.cancel()
        async_clear(self.hass, record.id)
        self.hass.bus.async_fire(
            f"{DOMAIN}_deleted", {"notification_id": record.id, "name": record.name}
        )
        self._broadcast("deleted", record, record.id)
        return {"id": record.id, "deleted": True}

    async def async_set_paused(self, record: NotificationRecord, paused: bool) -> dict[str, Any]:
        async with self._lock(record.id):
            self._require_current_record(record)
            self._ensure_not_delivering(record.id)
            record.paused = paused
            record.status = "paused" if paused else ("watching" if record.enabled else "disabled")
            record.updated_at = utc_iso()
            record.revision += 1
            event = "paused" if paused else "resumed"
            self._add_history(record, event, f"Conditional notification {event}")
            await self.store.async_save()
        await self.async_rebuild(record)
        self._event(event, record)
        return record.public_dict(dt_util.now())

    async def async_set_enabled(self, record: NotificationRecord, enabled: bool) -> dict[str, Any]:
        async with self._lock(record.id):
            self._require_current_record(record)
            self._ensure_not_delivering(record.id)
            record.enabled = enabled
            record.status = "paused" if record.paused else ("watching" if enabled else "disabled")
            record.updated_at = utc_iso()
            record.revision += 1
            self._add_history(record, "updated", "Enabled" if enabled else "Disabled")
            await self.store.async_save()
        await self.async_rebuild(record)
        self._event("updated", record)
        return record.public_dict(dt_util.now())

    def _seed_current_duration(
        self,
        runtime: RuntimeSubscriptions,
        definition: dict[str, Any],
        index: int,
        accepted: Any,
    ) -> None:
        """Begin a fresh proof period for a currently true duration trigger."""
        seconds = duration_seconds(definition.get("for"))
        if not seconds or definition["type"] not in {"state", "numeric_state"}:
            return

        kind = definition["type"]
        entity_id = definition["entity_id"]
        attribute = definition.get("attribute")
        state = self.hass.states.get(entity_id)
        if state is None:
            return

        if kind == "state":
            if "to" not in definition:
                return
            value = state_value(state, attribute)
            if value is None or is_unknown_state(value) or value != definition["to"]:
                return
            context = {
                "type": "state",
                "trigger_index": index,
                "entity_id": entity_id,
                "friendly_name": state.attributes.get("friendly_name", entity_id),
                "from_state": None,
                "to_state": value,
                "attribute": attribute,
                "matched_current_state": True,
            }

            def still_matches(current: Any) -> bool:
                current_value = state_value(current, attribute)
                return (
                    current_value is not None
                    and not is_unknown_state(current_value)
                    and current_value == definition["to"]
                )

        else:
            raw = state_value(state, attribute)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return
            if not math.isfinite(value) or not numeric_matches(value, definition):
                return
            context = {
                "type": "numeric_state",
                "trigger_index": index,
                "entity_id": entity_id,
                "friendly_name": state.attributes.get("friendly_name", entity_id),
                "previous_value": None,
                "value": value,
                "above": definition.get("above"),
                "below": definition.get("below"),
                "attribute": attribute,
                "matched_current_state": True,
            }

            def still_matches(current: Any) -> bool:
                raw_value = state_value(current, attribute)
                try:
                    current_value = float(raw_value)
                except (TypeError, ValueError):
                    return False
                return math.isfinite(current_value) and numeric_matches(current_value, definition)

        def duration_done() -> None:
            current = self.hass.states.get(entity_id)
            if not still_matches(current):
                return
            current_context = deepcopy(context)
            current_context["timestamp"] = dt_util.now().isoformat()
            accepted(current_context)

        runtime.schedule_duration(index, seconds, duration_done)

    async def async_rebuild(
        self,
        record: NotificationRecord,
        *,
        allow_current: bool = False,
        prove_current_durations: bool = False,
    ) -> None:
        if self._is_shutting_down():
            return
        current_record = self.store.records.get(record.id)
        if current_record is not record or current_record.revision != record.revision:
            return
        if old := self._runtimes.pop(record.id, None):
            old.cancel()
        runtime = RuntimeSubscriptions(self.hass, record.id, record.revision)
        self._runtimes[record.id] = runtime
        now = dt_util.now()
        expires = parse_datetime(record.definition.get("expires_at"))
        if expires:
            delay = (expires - now).total_seconds()
            if delay <= 0:
                await self._async_expire(record.id, record.revision)
                return
            runtime.add(
                async_call_later(
                    self.hass,
                    delay,
                    lambda _: self._schedule_task(self._async_expire(record.id, record.revision)),
                )
            )
        if (
            not record.enabled
            or record.paused
            or record.status in {"expired", "disabled", "resolved", "error"}
        ):
            return
        available = parse_datetime(record.definition.get("available_from"))
        if available and available > now:
            record.status = "watching"
            runtime.add(
                async_call_later(
                    self.hass,
                    (available - now).total_seconds(),
                    lambda _: self._schedule_task(self.async_rebuild(record)),
                )
            )
            return
        for index, definition in enumerate(record.definition["triggers"]):

            def accepted(
                context: dict[str, Any],
                record_id: str = record.id,
                revision: int = record.revision,
            ) -> None:
                self._schedule_task(self._async_trigger(record_id, revision, context))

            attach_trigger(runtime, definition, index, accepted)
            if prove_current_durations or (
                allow_current
                and record.definition.get("match_current_state")
                and definition["type"] == "state"
            ):
                self._seed_current_duration(runtime, definition, index, accepted)
        if resolve_definition := record.definition.get("resolve_when"):

            def resolved(
                context: dict[str, Any],
                record_id: str = record.id,
                revision: int = record.revision,
            ) -> None:
                self._schedule_task(self._async_resolve(record_id, revision, context))

            attach_trigger(
                runtime,
                resolve_definition,
                len(record.definition["triggers"]),
                resolved,
            )
            await self._async_resolve_if_current(record)
        if allow_current and record.definition.get("match_current_state"):
            await self._async_match_current(record)

    def _current_resolution_context(self, record: NotificationRecord) -> dict[str, Any] | None:
        """Describe a currently satisfied transition-like resolution conservatively."""
        definition = record.definition.get("resolve_when")
        if not definition or not record.active_occurrence:
            return None
        kind = definition["type"]
        if kind not in {"state", "numeric_state", "zone"}:
            return None
        entity_id = definition["entity_id"]
        current = self.hass.states.get(entity_id)
        if current is None:
            return None
        matched = False
        actual: Any = None
        if kind == "state":
            if "to" not in definition:
                return None
            actual = state_value(current, definition.get("attribute"))
            matched = (
                actual is not None and not is_unknown_state(actual) and actual == definition["to"]
            )
        elif kind == "numeric_state":
            raw = state_value(current, definition.get("attribute"))
            try:
                actual = float(raw)
            except (TypeError, ValueError):
                actual = None
            if actual is not None and not math.isfinite(actual):
                actual = None
            matched = numeric_matches(actual, definition)
        else:
            try:
                inside = zone_condition(
                    self.hass,
                    definition["zone_entity_id"],
                    current,
                )
            except ConditionError:
                return None
            matched = inside if definition["event"] == "enter" else not inside
            actual = current.state
        if not matched:
            return None
        return {
            "type": kind,
            "entity_id": entity_id,
            "current_value": actual,
            "matched_current_resolution": True,
            "timestamp": dt_util.now().isoformat(),
        }

    async def _async_resolve_if_current(self, record: NotificationRecord) -> None:
        if context := self._current_resolution_context(record):
            await self._async_resolve(record.id, record.revision, context)

    async def _async_match_current(self, record: NotificationRecord) -> None:
        for index, definition in enumerate(record.definition["triggers"]):
            if definition["type"] != "state" or "to" not in definition or definition.get("for"):
                continue
            state = self.hass.states.get(definition["entity_id"])
            value = state_value(state, definition.get("attribute"))
            if (
                state
                and value is not None
                and not is_unknown_state(value)
                and value == definition["to"]
            ):
                await self._async_trigger(
                    record.id,
                    record.revision,
                    {
                        "type": "state",
                        "trigger_index": index,
                        "entity_id": definition["entity_id"],
                        "friendly_name": state.attributes.get(
                            "friendly_name", definition["entity_id"]
                        ),
                        "from_state": None,
                        "to_state": value,
                        "attribute": definition.get("attribute"),
                        "timestamp": dt_util.now().isoformat(),
                        "matched_current_state": True,
                    },
                )
                return

    async def _async_persist_ignored(self, record: NotificationRecord, reason: str) -> None:
        record.last_ignored_reason = reason
        await self.store.async_save()
        self._broadcast("ignored", record, record.id)

    async def _async_rollback_cancelled_delivery(
        self,
        record_id: str,
        revision: int,
        accepted_at: str,
        completed: bool,
    ) -> None:
        async with self._lock(record_id):
            current = self.store.records.get(record_id)
            if (
                current is None
                or current.revision != revision
                or current.last_accepted_at != accepted_at
            ):
                return
            current.notification_count = max(0, current.notification_count - 1)
            current.last_accepted_at = None
            current.active_occurrence = False
            if completed:
                current.enabled = True
            current.status = (
                "paused" if current.paused else ("watching" if current.enabled else "disabled")
            )
            await self.store.async_save()

    async def _async_trigger(self, record_id: str, revision: int, trigger: dict[str, Any]) -> None:
        if self._is_shutting_down():
            return
        record = self.store.records.get(record_id)
        if not record:
            return
        delivery_key: tuple[str, int, str] | None = None
        accepted_at: str | None = None
        completed = False
        pending_resolution: dict[str, Any] | None = None
        try:
            async with self._lock(record_id):
                if record.revision != revision or not record.enabled or record.paused:
                    return
                now = dt_util.now()
                if not record.is_temporally_active(now) or record.status in {
                    "expired",
                    "disabled",
                    "resolved",
                }:
                    await self._async_persist_ignored(record, "Outside the active period")
                    return
                if record.active_occurrence:
                    await self._async_persist_ignored(
                        record, "Waiting for the active occurrence to resolve"
                    )
                    return
                if record.definition["repeat_policy"] == "limited" and record.remaining() == 0:
                    return
                debounce = duration_seconds(record.definition.get("debounce"))
                last_trigger_at = parse_datetime(record.last_trigger_at)
                if (
                    debounce
                    and last_trigger_at
                    and now < last_trigger_at + timedelta(seconds=debounce)
                ):
                    await self._async_persist_ignored(record, "Ignored by debounce")
                    return
                record.last_trigger_at = now.isoformat()
                record.last_trigger = trigger
                cooldown = duration_seconds(record.definition.get("cooldown"))
                last_accepted_at = parse_datetime(record.last_accepted_at)
                if (
                    cooldown
                    and last_accepted_at
                    and now < last_accepted_at + timedelta(seconds=cooldown)
                ):
                    await self._async_persist_ignored(record, "Cooldown is still active")
                    return
                conditions_passed, condition_results = async_evaluate_conditions(
                    self.hass, record.definition.get("conditions", []), now
                )
                if not conditions_passed:
                    record.last_ignored_reason = "A condition did not pass"
                    self._add_history(
                        record,
                        "ignored",
                        "Trigger ignored because a condition did not pass",
                        {"conditions": condition_results},
                    )
                    await self.store.async_save()
                    self._broadcast("ignored", record, record.id)
                    return
                accepted_at = now.isoformat()
                record.last_accepted_at = accepted_at
                record.qualifying_match_seen = True
                record.notification_count += 1
                record.last_ignored_reason = None
                record.active_occurrence = bool(record.definition.get("resolve_when"))
                record.status = "active" if record.active_occurrence else "triggered"
                policy = record.definition["repeat_policy"]
                completed = policy == "once" or (policy == "limited" and record.remaining() == 0)
                if completed and not record.active_occurrence:
                    record.enabled = False
                    record.status = "disabled"
                occurrence = record.notification_count
                self._add_history(
                    record,
                    "matched",
                    f"Qualifying occurrence {occurrence}",
                    {"trigger": trigger, "conditions": condition_results, "occurrence": occurrence},
                )
                delivery_key = (record_id, revision, accepted_at)
                self._delivery_store().add(delivery_key)
                task = asyncio.current_task()
                if task is not None:
                    self._delivery_task_store()[delivery_key] = task
                # Reserve the occurrence durably before provider side effects. A total
                # delivery/template failure rolls the count and completion state back.
                await self.store.async_save()
            try:
                title = await async_render(self.hass, record.definition["title"], trigger, record)
                message = await async_render(
                    self.hass, record.definition["message"], trigger, record
                )
                results = await async_deliver(
                    self.hass, record, title, message, self.options["delivery"]
                )
                delivered = any(r["success"] for r in results)
                event = "notification_sent" if delivered else "delivery_failed"
                summary = (
                    "Notification delivery completed"
                    if delivered
                    else "All delivery channels failed"
                )
                details = {
                    "trigger": trigger,
                    "title": title,
                    "message": message,
                    "delivery": results,
                    "occurrence": occurrence,
                }
            except (TemplateError, TypeError, ValueError) as err:
                results = [{"channel": "template", "success": False, "error": str(err)[:300]}]
                delivered = False
                event = "template_error"
                summary = "Notification template could not be rendered"
                details = {
                    "trigger": trigger,
                    "error": str(err)[:300],
                    "occurrence": occurrence,
                }
            async with self._lock(record_id):
                current = self.store.records.get(record_id)
                if not current or current.revision != revision:
                    return
                current.last_delivery = results
                if not delivered:
                    current.notification_count = max(0, current.notification_count - 1)
                    if current.last_accepted_at == accepted_at:
                        current.last_accepted_at = None
                    if current.active_occurrence:
                        current.active_occurrence = False
                    if completed:
                        current.enabled = True
                    current.status = (
                        "paused"
                        if current.paused
                        else ("watching" if current.enabled else "disabled")
                    )
                elif not current.active_occurrence and current.enabled:
                    current.status = "watching"
                self._add_history(current, event, summary, details)
                if delivery_key is not None:
                    self._delivery_store().discard(delivery_key)
                    pending_resolution = self._pending_resolution_store().pop(delivery_key, None)
                    self._delivery_task_store().pop(delivery_key, None)
                await self.store.async_save()
            if delivered:
                resolution_type = current.definition.get("resolve_when", {}).get("type")
                if pending_resolution is not None and resolution_type in {"event", "named"}:
                    await self._async_resolve(record_id, revision, pending_resolution)
                else:
                    await self._async_resolve_if_current(current)
            if self._is_shutting_down():
                return
            await self.async_rebuild(current)
            latest = self.store.records.get(record_id)
            if latest is current and latest.revision == revision:
                self._event("triggered", latest)
        except asyncio.CancelledError:
            if accepted_at is not None:
                await self._async_rollback_cancelled_delivery(
                    record_id, revision, accepted_at, completed
                )
            raise
        finally:
            if delivery_key is not None:
                self._delivery_store().discard(delivery_key)
                self._pending_resolution_store().pop(delivery_key, None)
                self._delivery_task_store().pop(delivery_key, None)

    async def _async_resolve(self, record_id: str, revision: int, trigger: dict[str, Any]) -> None:
        if self._is_shutting_down():
            return
        record = self.store.records.get(record_id)
        if not record:
            return
        async with self._lock(record_id):
            if record.revision != revision or not record.active_occurrence:
                return
            inflight = next(
                (
                    key
                    for key in self._delivery_store()
                    if key[0] == record_id and key[1] == revision
                ),
                None,
            )
            if inflight is not None:
                self._pending_resolution_store()[inflight] = deepcopy(trigger)
                return
            record.active_occurrence = False
            policy = record.definition["repeat_policy"]
            complete = policy == "once" or (policy == "limited" and record.remaining() == 0)
            record.enabled = not complete
            record.status = "resolved" if complete else "watching"
            self._add_history(
                record, "resolved", "Active occurrence resolved", {"trigger": trigger}
            )
            await self.store.async_save()
        if record.definition.get("clear_on_resolve", True):
            async_clear(self.hass, record.id)
        if source := record.definition.get("resolved_message"):
            try:
                title = await async_render(
                    self.hass,
                    record.definition.get("resolved_title", f"Resolved: {record.name}"),
                    trigger,
                    record,
                )
                message = await async_render(self.hass, source, trigger, record)
                results = await async_deliver(
                    self.hass,
                    record,
                    title,
                    message,
                    self.options["delivery"],
                )
                if not any(result["success"] for result in results):
                    async with self._lock(record_id):
                        current = self.store.records.get(record_id)
                        if not current or current.revision != revision:
                            return
                        self._add_history(
                            current,
                            "delivery_failed",
                            "All resolution delivery channels failed",
                            {"phase": "resolution", "delivery": results},
                        )
                        await self.store.async_save()
            except (TemplateError, TypeError, ValueError) as err:
                async with self._lock(record_id):
                    current = self.store.records.get(record_id)
                    if not current or current.revision != revision:
                        return
                    self._add_history(
                        current,
                        "template_error",
                        "Resolution template could not be rendered",
                        {"phase": "resolution", "error": str(err)[:300]},
                    )
                    await self.store.async_save()
        if self._is_shutting_down():
            return
        current = self.store.records.get(record_id)
        if not current or current.revision != revision:
            return
        await self.async_rebuild(current)
        latest = self.store.records.get(record_id)
        if latest is current and latest.revision == revision:
            self._event("resolved", latest)

    async def _async_expire(self, record_id: str, revision: int) -> None:
        if self._is_shutting_down():
            return
        record = self.store.records.get(record_id)
        if not record:
            return
        should_notify = False
        async with self._lock(record_id):
            if record.revision != revision or record.status == "expired":
                return
            should_notify = (
                bool(record.definition.get("notify_on_expiry")) and not record.qualifying_match_seen
            )
            record.status = "expired"
            record.enabled = False
            record.updated_at = utc_iso()
            self._add_history(record, "expired", "Observation window expired")
            await self.store.async_save()
            if runtime := self._runtimes.pop(record.id, None):
                runtime.cancel()
        if should_notify:
            trigger = {"type": "expiry", "timestamp": dt_util.now().isoformat()}
            try:
                title = await async_render(
                    self.hass,
                    record.definition.get("expiry_title", f"Expired: {record.name}"),
                    trigger,
                    record,
                )
                message = await async_render(
                    self.hass,
                    record.definition.get("expiry_message", "No qualifying event occurred."),
                    trigger,
                    record,
                )
                results = await async_deliver(
                    self.hass, record, title, message, self.options["delivery"]
                )
                async with self._lock(record_id):
                    current = self.store.records.get(record_id)
                    if not current or current.revision != revision or current.status != "expired":
                        return
                    success = any(result["success"] for result in results)
                    self._add_history(
                        current,
                        "expiry_notification" if success else "delivery_failed",
                        "No-event expiry notification sent"
                        if success
                        else "All no-event expiry delivery channels failed",
                        {"title": title, "message": message, "delivery": results},
                    )
                    await self.store.async_save()
            except (TemplateError, TypeError, ValueError) as err:
                async with self._lock(record_id):
                    current = self.store.records.get(record_id)
                    if not current or current.revision != revision or current.status != "expired":
                        return
                    self._add_history(
                        current,
                        "template_error",
                        "Expiry template failed",
                        {"error": str(err)[:300]},
                    )
                    await self.store.async_save()
        if self._is_shutting_down():
            return
        current = self.store.records.get(record_id)
        if current and current.revision == revision and current.status == "expired":
            self._event("expired", current)

    async def async_test(self, record: NotificationRecord) -> dict[str, Any]:
        self._require_current_record(record)
        trigger = record.last_trigger or {
            "type": "test",
            "friendly_name": "Test trigger",
            "timestamp": dt_util.now().isoformat(),
        }
        title = await async_render(self.hass, record.definition["title"], trigger, record)
        message = await async_render(self.hass, record.definition["message"], trigger, record)
        results = await async_deliver(
            self.hass,
            record,
            f"Test: {title}",
            message,
            self.options["delivery"],
            test=True,
        )
        return {
            "id": record.id,
            "test": True,
            "title": title,
            "message": message,
            "delivery": results,
        }

    async def async_trigger_now(self, record: NotificationRecord) -> dict[str, Any]:
        self._require_current_record(record)
        await self._async_trigger(
            record.id,
            record.revision,
            {
                "type": "manual",
                "friendly_name": "Manual trigger",
                "timestamp": dt_util.now().isoformat(),
            },
        )
        current = self.store.records.get(record.id)
        return current.public_dict(dt_util.now()) if current else {"id": record.id, "deleted": True}

    async def async_clear_history(self, notification_id: str | None = None) -> dict[str, Any]:
        before = len(self.store.history)
        if notification_id:
            self.store.history = [
                item for item in self.store.history if item.notification_id != notification_id
            ]
        else:
            self.store.history.clear()
        await self.store.async_save()
        return {"removed": before - len(self.store.history)}
