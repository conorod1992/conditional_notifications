"""Context-local state used while evaluating native Home Assistant conditions."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

CURRENT_TRIGGER: ContextVar[dict[str, Any] | None] = ContextVar(
    "conditional_notifications_current_trigger", default=None
)
CURRENT_CONDITION_CHECKERS: ContextVar[dict[int, Any] | None] = ContextVar(
    "conditional_notifications_condition_checkers", default=None
)
