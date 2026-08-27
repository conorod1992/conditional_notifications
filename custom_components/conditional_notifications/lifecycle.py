"""Extended lifecycle behaviour for re-arming, stable resolution, and correlation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .delivery import async_clear
from .manager import NotificationManager
from .models import NotificationRecord, duration_seconds, parse_datetime, utc_iso
from .triggers import schedule_task
from .validation import DefinitionError


class LifecycleNotificationManager(NotificationManager):
    """Add re-arm, stable resolution, and bounded trigger correlation."""

    def _correlation_store(
        self,
    ) -> dict[tuple[str, int], dict[int, tuple[datetime, dict[str, Any]]]]:
        store = getattr(self, "_trigger_correlations", None)
        if store is None:
            store = {}
            self._trigger_correlations = store
        return store

    def _clear_correlation(self, record_id: str) -> None:
        store = self._correlation_store()
        for key in [key for key in store if key[0] == record_id]:
            store.pop(key, None)

    @staticmethod
    def _definition_is_semantic_change(
        old_definition: dict[str, Any], new_definition: dict[str, Any]
    ) -> bool:
        return NotificationManager._definition_is_semantic_change(
            old_definition, new_definition
        ) or any(
            old_definition.get(key) != new_definition.get(key)
            for key in ("match", "match_window")
        )

    def _correlate_trigger(
        self,
        record: NotificationRecord,
        trigger: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return a combined trigger only when every trigger matched in-window."""
        if record.definition.get("match", "any") != "all_within":
            return trigger

        current = now or dt_util.now()
        trigger_count = len(record.definition.get("triggers", []))
        try:
            index = int(trigger["trigger_index"])
        except (KeyError, TypeError, ValueError):
            return None
        if index < 0 or index >= trigger_count:
            return None

        window = duration_seconds(record.definition.get("match_window"))
        key = (record.id, record.revision)
        store = self._correlation_store()
        matches = store.setdefault(key, {})
        cutoff = current.timestamp() - window
        for old_index, (matched_at, _) in list(matches.items()):
            if matched_at.timestamp() < cutoff:
                matches.pop(old_index, None)

        matches[index] = (current, deepcopy(trigger))
        if len(matches) < trigger_count:
            return None

        ordered = [matches[item] for item in range(trigger_count)]
        store.pop(key, None)
        first_at = min(item[0] for item in ordered)
        last_at = max(item[0] for item in ordered)
        combined = deepcopy(trigger)
        combined["matched_triggers"] = [item[1] for item in ordered]
        combined["correlation"] = {
            "match": "all_within",
            "window_seconds": window,
            "first_trigger_at": first_at.isoformat(),
            "last_trigger_at": last_at.isoformat(),
        }
        return combined

    async def _async_trigger(
        self, record_id: str, revision: int, trigger: dict[str, Any]
    ) -> None:
        record = self.store.records.get(record_id)
        if record is None or record.revision != revision:
            return
        if record.definition.get("match", "any") == "any":
            await super()._async_trigger(record_id, revision, trigger)
            return
        if not record.enabled or record.paused:
            return
        combined = self._correlate_trigger(record, trigger)
        if combined is None:
            return
        await super()._async_trigger(record_id, revision, combined)

    async def async_rebuild(
        self, record: NotificationRecord, *, allow_current: bool = False
    ) -> None:
        # Partial correlations are deliberately in-memory only. A rebuild or
        # restart starts a fresh correlation window rather than joining events
        # across an uncertain subscription gap.
        self._clear_correlation(record.id)
        await super().async_rebuild(record, allow_current=allow_current)

    async def async_rearm(self, record: NotificationRecord) -> dict[str, Any]:
        """Reset runtime progress and begin a fresh observation cycle."""
        now = dt_util.now()
        expires = parse_datetime(record.definition.get("expires_at"))
        if expires and expires <= now:
            raise DefinitionError(
                "expires_at",
                "is in the past; edit the expiry before re-arming",
            )

        async with self._lock(record.id):
            record.revision += 1
            self._reset_runtime_state(record)
            record.enabled = True
            record.paused = False
            record.status = "watching"
            record.updated_at = utc_iso()
            self._add_history(
                record,
                "rearmed",
                "Conditional notification re-armed; runtime progress reset",
            )
            await self.store.async_save()

        self._clear_correlation(record.id)
        # A previous active occurrence may have created a tagged persistent
        # notification. Re-arming explicitly abandons that occurrence.
        async_clear(self.hass, record.id)
        await self.async_rebuild(record)
        self._event("rearmed", record)
        return record.public_dict(dt_util.now())

    async def _async_resolve_if_current(self, record: NotificationRecord) -> None:
        """Resolve current matches, respecting an optional stability duration."""
        context = self._current_resolution_context(record)
        if context is None:
            return

        definition = record.definition.get("resolve_when", {})
        seconds = duration_seconds(definition.get("for"))
        if not seconds:
            await self._async_resolve(record.id, record.revision, context)
            return

        runtime = self._runtimes.get(record.id)
        if runtime is None or runtime.revision != record.revision:
            return

        resolution_index = len(record.definition["triggers"])

        def duration_done(
            record_id: str = record.id,
            revision: int = record.revision,
        ) -> None:
            schedule_task(
                self.hass,
                self._async_resolve_after_duration(record_id, revision),
            )

        # If Home Assistant restarted, or the alert became active while the
        # resolution condition was already true, begin proving the duration
        # from now rather than assuming time before this point was continuous.
        runtime.schedule_duration(resolution_index, seconds, duration_done)

    async def _async_resolve_after_duration(self, record_id: str, revision: int) -> None:
        """Resolve only if the condition still matches after the full duration."""
        record = self.store.records.get(record_id)
        if record is None or record.revision != revision or not record.active_occurrence:
            return

        context = self._current_resolution_context(record)
        if context is None:
            return
        context["resolution_duration_elapsed"] = True
        await self._async_resolve(record_id, revision, context)
