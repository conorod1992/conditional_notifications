"""Extended lifecycle behaviour for re-arming and stable resolution."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .delivery import async_clear
from .manager import NotificationManager
from .models import NotificationRecord, duration_seconds, parse_datetime, utc_iso
from .triggers import schedule_task
from .validation import DefinitionError


class LifecycleNotificationManager(NotificationManager):
    """Add explicit re-arm semantics and duration-aware resolution."""

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
