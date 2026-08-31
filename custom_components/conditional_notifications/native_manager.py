"""Native Home Assistant automation support layered onto notification lifecycle."""

from __future__ import annotations

import logging
import math
from copy import deepcopy
from typing import Any

from homeassistant.components.zone.condition import zone as zone_condition
from homeassistant.exceptions import ConditionError, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .conditions import is_unknown_state, numeric_matches, state_value
from .delivery import async_clear
from .lifecycle import LifecycleNotificationManager as LegacyLifecycleNotificationManager
from .manager import NotificationManager
from .models import NotificationRecord, parse_datetime, utc_iso
from .native_automation import (
    async_attach_native_resolution_trigger,
    async_attach_native_trigger,
    async_build_native_condition_checkers,
    async_validate_native_definition,
    is_native_trigger,
    legacy_trigger_view,
)
from .native_context import CURRENT_CONDITION_CHECKERS, CURRENT_TRIGGER
from .native_security import async_validate_native_observation_access
from .native_validation import validate_definition
from .security import async_validate_observation_access
from .triggers import RuntimeSubscriptions, attach_trigger
from .validation import DefinitionError

_LOGGER = logging.getLogger(__name__)


class LifecycleNotificationManager(LegacyLifecycleNotificationManager):
    """Use HA-native trigger/condition machinery while preserving notification lifecycle."""

    def _native_pending_store(self) -> dict[tuple[str, int], dict[str, Any]]:
        store = getattr(self, "_native_pending_resolutions", None)
        if store is None:
            store = {}
            self._native_pending_resolutions = store
        return store

    async def _async_validate_definition_for_owner(
        self, definition: dict[str, Any], owner_id: str | None
    ) -> dict[str, Any]:
        normalized = validate_definition(definition)
        await async_validate_native_definition(self.hass, normalized)
        await async_validate_observation_access(self.hass, normalized, owner_id)
        await async_validate_native_observation_access(self.hass, normalized, owner_id)
        self._validate_templates(normalized)
        return normalized

    async def async_initialize(self) -> None:
        """Load both legacy and native definitions without migrating stored records."""
        self._shutting_down = False
        await self.store.async_load()
        self._prune_history()
        quarantined = False
        for record in list(self.store.records.values()):
            try:
                normalized = await self._async_validate_definition_for_owner(
                    record.definition, record.owner_id
                )
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
        if quarantined:
            await self.store.async_save()

    async def async_shutdown(self) -> None:
        self._native_pending_store().clear()
        await super().async_shutdown()

    async def async_create(
        self, definition: dict[str, Any], owner_id: str | None
    ) -> dict[str, Any]:
        """Validate native config through HA before any durable mutation."""
        normalized = await self._async_validate_definition_for_owner(definition, owner_id)
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
        """Validate and atomically replace mixed legacy/native definitions."""
        async with self._lock(record.id):
            self._require_current_record(record)
            self._require_expected_revision(record, expected_revision)
            self._ensure_not_delivering(record.id)
            merged = deepcopy(record.definition)
            merged.update(changes)
            merged["name"] = changes.get("name", record.name)
            normalized = await self._async_validate_definition_for_owner(
                merged, record.owner_id
            )
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

    def _seed_current_duration(
        self,
        runtime: RuntimeSubscriptions,
        definition: dict[str, Any],
        index: int,
        accepted: Any,
    ) -> None:
        projected = legacy_trigger_view(definition)
        if projected is not None:
            super()._seed_current_duration(runtime, projected, index, accepted)

    async def async_rebuild(
        self,
        record: NotificationRecord,
        *,
        allow_current: bool = False,
        prove_current_durations: bool = False,
    ) -> None:
        """Attach legacy and HA-native subscriptions into one revision-owned runtime."""
        await async_validate_observation_access(self.hass, record.definition, record.owner_id)
        await async_validate_native_observation_access(self.hass, record.definition, record.owner_id)
        self._clear_correlation(record.id)
        self._native_pending_store().pop((record.id, record.revision), None)

        if self._is_shutting_down():
            return
        current_record = self.store.records.get(record.id)
        if current_record is not record or current_record.revision != record.revision:
            return
        if old := self._runtimes.pop(record.id, None):
            old.cancel()
        runtime = RuntimeSubscriptions(self.hass, record.id, record.revision)
        self._runtimes[record.id] = runtime

        try:
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
                        lambda _: self._schedule_task(
                            self._async_expire(record.id, record.revision)
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
                        lambda _: self._schedule_task(self.async_rebuild(record)),
                    )
                )
                return

            runtime.native_condition_checkers = await async_build_native_condition_checkers(
                self.hass,
                runtime,
                record.definition.get("conditions", []),
            )

            for index, definition in enumerate(record.definition["triggers"]):

                def accepted(
                    context: dict[str, Any],
                    record_id: str = record.id,
                    revision: int = record.revision,
                ) -> None:
                    self._schedule_task(self._async_trigger(record_id, revision, context))

                if is_native_trigger(definition):
                    await async_attach_native_trigger(
                        runtime, definition, index, accepted, record.name
                    )
                else:
                    attach_trigger(runtime, definition, index, accepted)

                projected = legacy_trigger_view(definition)
                if projected is not None and (
                    prove_current_durations
                    or (
                        allow_current
                        and record.definition.get("match_current_state")
                        and projected.get("type") == "state"
                    )
                ):
                    self._seed_current_duration(runtime, definition, index, accepted)

            if resolve_definition := record.definition.get("resolve_when"):

                def resolved(
                    context: dict[str, Any],
                    record_id: str = record.id,
                    revision: int = record.revision,
                ) -> None:
                    self._schedule_task(self._async_resolve(record_id, revision, context))

                resolution_index = len(record.definition["triggers"])
                if is_native_trigger(resolve_definition):
                    await async_attach_native_resolution_trigger(
                        runtime,
                        resolve_definition,
                        resolution_index,
                        resolved,
                        record.name,
                    )
                else:
                    attach_trigger(runtime, resolve_definition, resolution_index, resolved)
                await self._async_resolve_if_current(record)

            if allow_current and record.definition.get("match_current_state"):
                await self._async_match_current(record)
        except Exception:
            current = self._runtimes.get(record.id)
            if current is runtime:
                self._runtimes.pop(record.id, None)
            runtime.cancel()
            raise

    async def _async_trigger(
        self, record_id: str, revision: int, trigger: dict[str, Any]
    ) -> None:
        """Apply correlation, then expose the final trigger to native conditions."""
        if self._is_shutting_down():
            return
        record = self.store.records.get(record_id)
        if record is None or record.revision != revision:
            return

        final_trigger = trigger
        if trigger.get("type") != "manual" and record.definition.get("match", "any") != "any":
            if not record.enabled or record.paused:
                return
            correlated = self._correlate_trigger(record, trigger)
            if correlated is None:
                return
            final_trigger = correlated

        runtime = self._runtimes.get(record_id)
        checkers = (
            getattr(runtime, "native_condition_checkers", {})
            if runtime is not None and runtime.revision == revision
            else {}
        )
        trigger_token = CURRENT_TRIGGER.set(final_trigger)
        checker_token = CURRENT_CONDITION_CHECKERS.set(checkers)
        try:
            await NotificationManager._async_trigger(self, record_id, revision, final_trigger)
        finally:
            CURRENT_CONDITION_CHECKERS.reset(checker_token)
            CURRENT_TRIGGER.reset(trigger_token)

        pending = self._native_pending_store().pop((record_id, revision), None)
        current = self.store.records.get(record_id)
        if pending is not None and current is not None and current.active_occurrence:
            await self._async_resolve(record_id, revision, pending)

    async def _async_resolve(
        self, record_id: str, revision: int, trigger: dict[str, Any]
    ) -> None:
        """Retain transient native resolution events that occur during delivery."""
        record = self.store.records.get(record_id)
        resolve_when = record.definition.get("resolve_when") if record else None
        if (
            record is not None
            and record.revision == revision
            and record.active_occurrence
            and isinstance(resolve_when, dict)
            and is_native_trigger(resolve_when)
            and legacy_trigger_view(resolve_when) is None
            and any(key[0] == record_id and key[1] == revision for key in self._delivery_store())
        ):
            self._native_pending_store()[(record_id, revision)] = deepcopy(trigger)
        await super()._async_resolve(record_id, revision, trigger)

    def _current_resolution_context(self, record: NotificationRecord) -> dict[str, Any] | None:
        """Evaluate current-state semantics only for safely projectable native triggers."""
        source = record.definition.get("resolve_when")
        if not isinstance(source, dict) or not record.active_occurrence:
            return None
        definition = legacy_trigger_view(source)
        if definition is None:
            return None
        kind = definition.get("type")
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
                actual is not None
                and not is_unknown_state(actual)
                and actual == definition["to"]
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

    async def _async_match_current(self, record: NotificationRecord) -> None:
        """Seed projectable state triggers without inventing semantics for other HA types."""
        correlated = record.definition.get("match", "any") == "all_within"
        for index, source in enumerate(record.definition["triggers"]):
            definition = legacy_trigger_view(source)
            if (
                definition is None
                or definition.get("type") != "state"
                or "to" not in definition
                or definition.get("for")
            ):
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
                if not correlated:
                    return
