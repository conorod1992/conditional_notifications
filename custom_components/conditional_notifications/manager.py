"""Race-safe lifecycle manager for persistent event-driven notifications."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.template import Template, TemplateError
from homeassistant.util import dt as dt_util

from .conditions import async_evaluate_conditions
from .const import DEFAULT_OPTIONS, DOMAIN, EVENTS, SIGNAL_CHANGED
from .delivery import async_clear, async_deliver, async_render
from .models import HistoryItem, NotificationRecord, duration_seconds, parse_datetime, utc_iso
from .storage import NotificationStore
from .triggers import RuntimeSubscriptions, attach_trigger, schedule_task
from .validation import DefinitionError, validate_definition


class NotFound(HomeAssistantError):
    """Requested record was not found."""


class AmbiguousReference(HomeAssistantError):
    """A safe mutating lookup found multiple candidates."""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        super().__init__("Reference is ambiguous")
        self.candidates = candidates


class NotificationManager:
    """Own records, subscriptions, timers, history, and delivery transitions."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = {**DEFAULT_OPTIONS, **options}
        self.store = NotificationStore(hass)
        self._runtimes: dict[str, RuntimeSubscriptions] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: set[Any] = set()

    async def async_initialize(self) -> None:
        await self.store.async_load()
        self._prune_history()
        for record in list(self.store.records.values()):
            await self.async_rebuild(record)

    async def async_shutdown(self) -> None:
        for runtime in self._runtimes.values():
            runtime.cancel()
        self._runtimes.clear()
        await self.store.async_save()

    @callback
    def subscribe(self, listener: Any) -> Any:
        self._subscribers.add(listener)

        @callback
        def unsubscribe() -> None:
            self._subscribers.discard(listener)

        return unsubscribe

    def _broadcast(self, event: str, record: NotificationRecord | None, record_id: str) -> None:
        payload = {
            "event": event,
            "notification_id": record_id,
            "record": record.public_dict(dt_util.now()) if record else None,
            "owner_id": record.owner_id if record else None,
        }
        for listener in list(self._subscribers):
            listener(payload)
        async_dispatcher_send(self.hass, SIGNAL_CHANGED)

    def _event(self, event: str, record: NotificationRecord) -> None:
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
            HistoryItem.create(record.id, event, summary, details),
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
        for field in ("title", "message", "expiry_title", "expiry_message", "resolved_message"):
            if source := definition.get(field):
                try:
                    Template(str(source), self.hass).ensure_valid()
                except TemplateError as err:
                    raise DefinitionError(field, str(err)) from err

    def _lock(self, record_id: str) -> asyncio.Lock:
        return self._locks.setdefault(record_id, asyncio.Lock())

    def can_access(self, record: NotificationRecord, user_id: str | None, is_admin: bool) -> bool:
        return is_admin or record.owner_id is None or record.owner_id == user_id

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
                    term in str(t.get("entity_id", "")).casefold() for t in r.definition["triggers"]
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
        await self.async_rebuild(record, allow_current=True)
        self._event("created", record)
        return record.public_dict(dt_util.now())

    async def async_update(
        self, record: NotificationRecord, changes: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._lock(record.id):
            merged = deepcopy(record.definition)
            merged.update(changes)
            merged["name"] = changes.get("name", record.name)
            normalized = validate_definition(merged)
            self._validate_templates(normalized)
            record.revision += 1
            requested_enabled = bool(normalized.pop("enabled", record.enabled))
            record.definition = normalized
            record.name = normalized["name"]
            record.semantic_key = normalized.get("semantic_key")
            record.description = normalized.get("description")
            if "enabled" in changes:
                record.enabled = requested_enabled
            record.updated_at = utc_iso()
            record.status = (
                "paused" if record.paused else ("watching" if record.enabled else "disabled")
            )
            record.last_ignored_reason = None
            self._add_history(record, "updated", "Definition updated")
            await self.store.async_save()
        await self.async_rebuild(record)
        self._event("updated", record)
        return record.public_dict(dt_util.now())

    async def async_duplicate(
        self, record: NotificationRecord, owner_id: str | None, name: str | None = None
    ) -> dict[str, Any]:
        definition = deepcopy(record.definition)
        definition["name"] = name or f"{record.name} copy"
        definition.pop("semantic_key", None)
        return await self.async_create(definition, owner_id)

    async def async_delete(self, record: NotificationRecord) -> dict[str, Any]:
        async with self._lock(record.id):
            if runtime := self._runtimes.pop(record.id, None):
                runtime.cancel()
            self.store.records.pop(record.id, None)
            self._locks.pop(record.id, None)
            self._add_history(record, "deleted", "Conditional notification deleted")
            await self.store.async_save()
        self.hass.bus.async_fire(
            f"{DOMAIN}_deleted", {"notification_id": record.id, "name": record.name}
        )
        self._broadcast("deleted", record, record.id)
        return {"id": record.id, "deleted": True}

    async def async_set_paused(self, record: NotificationRecord, paused: bool) -> dict[str, Any]:
        async with self._lock(record.id):
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
            record.enabled = enabled
            record.status = "paused" if record.paused else ("watching" if enabled else "disabled")
            record.updated_at = utc_iso()
            record.revision += 1
            self._add_history(record, "updated", "Enabled" if enabled else "Disabled")
            await self.store.async_save()
        await self.async_rebuild(record)
        self._event("updated", record)
        return record.public_dict(dt_util.now())

    async def async_rebuild(
        self, record: NotificationRecord, *, allow_current: bool = False
    ) -> None:
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
                    lambda _: schedule_task(
                        self.hass, self._async_expire(record.id, record.revision)
                    ),
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
                    lambda _: schedule_task(self.hass, self.async_rebuild(record)),
                )
            )
            return
        for index, definition in enumerate(record.definition["triggers"]):

            def accepted(
                context: dict[str, Any],
                record_id: str = record.id,
                revision: int = record.revision,
            ) -> None:
                schedule_task(self.hass, self._async_trigger(record_id, revision, context))

            attach_trigger(
                runtime,
                definition,
                index,
                accepted,
            )
        if resolve_definition := record.definition.get("resolve_when"):

            def resolved(
                context: dict[str, Any],
                record_id: str = record.id,
                revision: int = record.revision,
            ) -> None:
                schedule_task(self.hass, self._async_resolve(record_id, revision, context))

            attach_trigger(
                runtime,
                resolve_definition,
                len(record.definition["triggers"]),
                resolved,
            )
        if allow_current and record.definition.get("match_current_state"):
            await self._async_match_current(record)

    async def _async_match_current(self, record: NotificationRecord) -> None:
        for index, definition in enumerate(record.definition["triggers"]):
            if definition["type"] != "state" or "to" not in definition or definition.get("for"):
                continue
            state = self.hass.states.get(definition["entity_id"])
            if state and state.state == definition["to"]:
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
                        "to_state": state.state,
                        "timestamp": dt_util.now().isoformat(),
                        "matched_current_state": True,
                    },
                )
                return

    async def _async_trigger(self, record_id: str, revision: int, trigger: dict[str, Any]) -> None:
        record = self.store.records.get(record_id)
        if not record:
            return
        async with self._lock(record_id):
            if record.revision != revision or not record.enabled or record.paused:
                return
            now = dt_util.now()
            if not record.is_temporally_active(now) or record.status in {
                "expired",
                "disabled",
                "resolved",
            }:
                record.last_ignored_reason = "Outside the active period"
                return
            if record.active_occurrence:
                record.last_ignored_reason = "Waiting for the active occurrence to resolve"
                return
            if record.definition["repeat_policy"] == "limited" and record.remaining() == 0:
                return
            debounce = duration_seconds(record.definition.get("debounce"))
            last_trigger_at = parse_datetime(record.last_trigger_at)
            if debounce and last_trigger_at and now < last_trigger_at + timedelta(seconds=debounce):
                record.last_ignored_reason = "Ignored by debounce"
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
                record.last_ignored_reason = "Cooldown is still active"
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
            record.last_accepted_at = now.isoformat()
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
            # Acceptance and count are durable before provider side effects.
            await self.store.async_save()
        try:
            title = await async_render(self.hass, record.definition["title"], trigger, record)
            message = await async_render(self.hass, record.definition["message"], trigger, record)
            results = await async_deliver(
                self.hass, record, title, message, self.options["delivery"]
            )
            event = "notification_sent" if any(r["success"] for r in results) else "delivery_failed"
            summary = (
                "Notification delivery completed"
                if event == "notification_sent"
                else "All delivery channels failed"
            )
            details = {
                "trigger": trigger,
                "title": title,
                "message": message,
                "delivery": results,
                "occurrence": occurrence,
            }
        except (TemplateError, ValueError) as err:
            results = [{"channel": "template", "success": False, "error": str(err)[:300]}]
            event, summary = "template_error", "Notification template could not be rendered"
            details = {"trigger": trigger, "error": str(err)[:300], "occurrence": occurrence}
        async with self._lock(record_id):
            current = self.store.records.get(record_id)
            if not current or current.revision != revision:
                return
            current.last_delivery = results
            if not current.active_occurrence and current.enabled:
                current.status = "watching"
            self._add_history(current, event, summary, details)
            await self.store.async_save()
        await self.async_rebuild(record)
        self._event("triggered", record)

    async def _async_resolve(self, record_id: str, revision: int, trigger: dict[str, Any]) -> None:
        record = self.store.records.get(record_id)
        if not record:
            return
        async with self._lock(record_id):
            if record.revision != revision or not record.active_occurrence:
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
                message = await async_render(self.hass, source, trigger, record)
                await async_deliver(
                    self.hass,
                    record,
                    record.definition.get("resolved_title", f"Resolved: {record.name}"),
                    message,
                    self.options["delivery"],
                )
            except (TemplateError, ValueError):
                pass
        await self.async_rebuild(record)
        self._event("resolved", record)

    async def _async_expire(self, record_id: str, revision: int) -> None:
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
                    self._add_history(
                        record,
                        "expiry_notification",
                        "No-event expiry notification sent",
                        {"title": title, "message": message, "delivery": results},
                    )
                    await self.store.async_save()
            except (TemplateError, ValueError) as err:
                async with self._lock(record_id):
                    self._add_history(
                        record,
                        "template_error",
                        "Expiry template failed",
                        {"error": str(err)[:300]},
                    )
                    await self.store.async_save()
        self._event("expired", record)

    async def async_test(self, record: NotificationRecord) -> dict[str, Any]:
        trigger = record.last_trigger or {
            "type": "test",
            "friendly_name": "Test trigger",
            "timestamp": dt_util.now().isoformat(),
        }
        title = await async_render(self.hass, record.definition["title"], trigger, record)
        message = await async_render(self.hass, record.definition["message"], trigger, record)
        results = await async_deliver(
            self.hass, record, f"Test: {title}", message, self.options["delivery"], test=True
        )
        return {
            "id": record.id,
            "test": True,
            "title": title,
            "message": message,
            "delivery": results,
        }

    async def async_trigger_now(self, record: NotificationRecord) -> dict[str, Any]:
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
