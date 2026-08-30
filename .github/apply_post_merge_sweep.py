from __future__ import annotations

import re
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one literal match, found {count}\n---\n{old}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def sub_once(path: str, pattern: str, replacement: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex match, found {count}: {pattern}")
    file.write_text(updated, encoding="utf-8")


# Attribute state values may be structured, so unknown checks must be hash-safe.
# Runtime numeric values also fail closed for NaN/Infinity.
replace_once(
    "custom_components/conditional_notifications/conditions.py",
    "from datetime import datetime, time\nfrom typing import Any\n",
    "from datetime import datetime, time\nimport math\nfrom typing import Any\n",
)
replace_once(
    "custom_components/conditional_notifications/conditions.py",
    "from .const import UNKNOWN_STATES, WEEKDAYS\n\n\ndef _numeric_value",
    '''from .const import UNKNOWN_STATES, WEEKDAYS


def is_unknown_state(value: Any) -> bool:
    """Return whether a scalar state value is HA unknown/unavailable."""
    return isinstance(value, str) and value in UNKNOWN_STATES


def _numeric_value''',
)
replace_once(
    "custom_components/conditional_notifications/conditions.py",
    '''    if raw in UNKNOWN_STATES or raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
''',
    '''    if raw is None or is_unknown_state(raw):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
''',
)
replace_once(
    "custom_components/conditional_notifications/conditions.py",
    "            known = actual is not None and actual not in UNKNOWN_STATES\n",
    "            known = actual is not None and not is_unknown_state(actual)\n",
)

# Align from-only state `for:` continuity with Home Assistant's semantics.
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    "from collections.abc import Callable\nfrom datetime import datetime\nfrom typing import Any\n",
    "from collections.abc import Callable\nfrom datetime import datetime\nimport math\nfrom typing import Any\n",
)
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    "from .conditions import numeric_matches, state_value\n",
    "from .conditions import is_unknown_state, numeric_matches, state_value\n",
)
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    "    if new_value in UNKNOWN_STATES or new_value is None or old_value == new_value:\n",
    "    if new_value is None or is_unknown_state(new_value) or old_value == new_value:\n",
)
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    '''def _numeric_match(
    definition: dict[str, Any], old: State | None, new: State | None
) -> tuple[bool, float | None, float | None]:
''',
    '''def _state_still_matches(definition: dict[str, Any], state: State | None) -> bool:
    """Return whether a pending state duration remains continuously valid."""
    value = state_value(state, definition.get("attribute"))
    if value is None or is_unknown_state(value):
        return False
    if "to" in definition:
        return value == definition["to"]
    return "from" in definition and value != definition["from"]


def _numeric_match(
    definition: dict[str, Any], old: State | None, new: State | None
) -> tuple[bool, float | None, float | None]:
''',
)
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    '''        if raw in UNKNOWN_STATES or raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
''',
    '''        if raw is None or is_unknown_state(raw):
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
''',
)
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    '''                if kind == "state":
                    value = state_value(new, definition.get("attribute"))
                    still_matching = "to" in definition and value == definition["to"]
''',
    '''                if kind == "state":
                    still_matching = _state_still_matches(definition, new)
''',
)
replace_once(
    "custom_components/conditional_notifications/triggers.py",
    '''                if kind == "state":
                    value = state_value(current, definition.get("attribute"))
                    still_matches = "to" not in definition or value == definition["to"]
''',
    '''                if kind == "state":
                    still_matches = _state_still_matches(definition, current)
''',
)

# Close current-state proof and stale persistent-notification cleanup gaps.
replace_once(
    "custom_components/conditional_notifications/manager.py",
    "import asyncio\nimport logging\n",
    "import asyncio\nimport logging\nimport math\n",
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    "from .conditions import async_evaluate_conditions, numeric_matches, state_value\n",
    '''from .conditions import (
    async_evaluate_conditions,
    is_unknown_state,
    numeric_matches,
    state_value,
)
''',
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    '''            semantic_change = self._definition_is_semantic_change(record.definition, normalized)
            naturally_completed = self._completed_naturally(record)
            previous_status = record.status
''',
    '''            semantic_change = self._definition_is_semantic_change(record.definition, normalized)
            naturally_completed = self._completed_naturally(record)
            previous_status = record.status
            abandoned_active_occurrence = semantic_change and record.active_occurrence
''',
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    '''            await self.store.async_save()
        await self.async_rebuild(record)
        self._event("updated", record)
''',
    '''            await self.store.async_save()
        if abandoned_active_occurrence:
            async_clear(self.hass, record.id)
        await self.async_rebuild(record)
        self._event("updated", record)
''',
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    '''        for task in delivery_tasks:
            task.cancel()
        self.hass.bus.async_fire(
''',
    '''        for task in delivery_tasks:
            task.cancel()
        async_clear(self.hass, record.id)
        self.hass.bus.async_fire(
''',
)

new_seed = '''    def _seed_current_duration(
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
                return math.isfinite(current_value) and numeric_matches(
                    current_value, definition
                )

        def duration_done() -> None:
            current = self.hass.states.get(entity_id)
            if not still_matches(current):
                return
            current_context = deepcopy(context)
            current_context["timestamp"] = dt_util.now().isoformat()
            accepted(current_context)

        runtime.schedule_duration(index, seconds, duration_done)

'''
sub_once(
    "custom_components/conditional_notifications/manager.py",
    r"    def _seed_current_duration\(.*?(?=    async def async_rebuild\()",
    new_seed,
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    '''            attach_trigger(runtime, definition, index, accepted)
            if prove_current_durations:
                self._seed_current_duration(runtime, definition, index, accepted)
''',
    '''            attach_trigger(runtime, definition, index, accepted)
            if prove_current_durations or (
                allow_current
                and record.definition.get("match_current_state")
                and definition["type"] == "state"
            ):
                self._seed_current_duration(runtime, definition, index, accepted)
''',
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    '''            actual = state_value(current, definition.get("attribute"))
            matched = actual not in UNKNOWN_STATES and actual == definition["to"]
''',
    '''            actual = state_value(current, definition.get("attribute"))
            matched = (
                actual is not None
                and not is_unknown_state(actual)
                and actual == definition["to"]
            )
''',
)
replace_once(
    "custom_components/conditional_notifications/manager.py",
    '''            try:
                actual = float(raw)
            except (TypeError, ValueError):
                actual = None
            matched = numeric_matches(actual, definition)
''',
    '''            try:
                actual = float(raw)
            except (TypeError, ValueError):
                actual = None
            if actual is not None and not math.isfinite(actual):
                actual = None
            matched = numeric_matches(actual, definition)
''',
)
sub_once(
    "custom_components/conditional_notifications/manager.py",
    r'''            state = self\.hass\.states\.get\(definition\["entity_id"\]\)
            if state and state\.state == definition\["to"\]:
                await self\._async_trigger\(
                    record\.id,
                    record\.revision,
                    \{
                        "type": "state",
                        "trigger_index": index,
                        "entity_id": definition\["entity_id"\],
                        "friendly_name": state\.attributes\.get\(
                            "friendly_name", definition\["entity_id"\]
                        \),
                        "from_state": None,
                        "to_state": state\.state,
                        "timestamp": dt_util\.now\(\)\.isoformat\(\),
                        "matched_current_state": True,
                    \},
                \)
''',
    '''            state = self.hass.states.get(definition["entity_id"])
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
''',
)

replace_once(
    "custom_components/conditional_notifications/lifecycle.py",
    "from .delivery import async_clear\n",
    "from .conditions import is_unknown_state, state_value\nfrom .delivery import async_clear\n",
)
sub_once(
    "custom_components/conditional_notifications/lifecycle.py",
    r'''            state = self\.hass\.states\.get\(definition\["entity_id"\]\)
            if state and state\.state == definition\["to"\]:
                await self\._async_trigger\(
                    record\.id,
                    record\.revision,
                    \{
                        "type": "state",
                        "trigger_index": index,
                        "entity_id": definition\["entity_id"\],
                        "friendly_name": state\.attributes\.get\(
                            "friendly_name", definition\["entity_id"\]
                        \),
                        "from_state": None,
                        "to_state": state\.state,
                        "timestamp": dt_util\.now\(\)\.isoformat\(\),
                        "matched_current_state": True,
                    \},
                \)
''',
    '''            state = self.hass.states.get(definition["entity_id"])
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
''',
)

# Finish canonical entity-ID and scalar-state validation from the first sweep.
replace_once(
    "custom_components/conditional_notifications/validation.py",
    "from urllib.parse import urlparse\n\nfrom .const import WEEKDAYS\n",
    '''from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import WEEKDAYS
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    "def _finite_number(value: Any, path: str) -> float:\n",
    '''def _entity_id(value: Any, path: str, *, domain: str | None = None) -> str:
    try:
        entity_id = cv.entity_id(value)
    except vol.Invalid:
        _error(path, "must be a valid entity ID")
    if domain is not None and entity_id.split(".", 1)[0] != domain:
        _error(path, f"must be a {domain} entity")
    return entity_id


def _validate_attribute(data: dict[str, Any], path: str) -> None:
    if "attribute" not in data:
        return
    if not isinstance(data["attribute"], str) or not data["attribute"]:
        _error(path, "must be a non-empty attribute name")


def _finite_number(value: Any, path: str) -> float:
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    '''    if kind in {"state", "numeric_state", "zone"} and not result.get("entity_id"):
        _error(f"{path}.entity_id", "is required")
    if kind == "state":
''',
    '''    if kind in {"state", "numeric_state", "zone"}:
        result["entity_id"] = _entity_id(result.get("entity_id"), f"{path}.entity_id")
    if kind in {"state", "numeric_state"}:
        _validate_attribute(result, f"{path}.attribute")
    if kind == "state":
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    '''        if result.get("from") == result.get("to") and "from" in result and "to" in result:
            _error(path, "from and to must differ")
''',
    '''        if result.get("from") == result.get("to") and "from" in result and "to" in result:
            _error(path, "from and to must differ")
        if "attribute" not in result:
            for key in ("from", "to"):
                if key in result and not isinstance(result[key], str):
                    _error(
                        f"{path}.{key}",
                        "must be a string when no attribute is selected",
                    )
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    '''    elif kind == "zone":
        if not result.get("zone_entity_id", "").startswith("zone."):
            _error(f"{path}.zone_entity_id", "must be a zone entity")
''',
    '''    elif kind == "zone":
        result["zone_entity_id"] = _entity_id(
            result.get("zone_entity_id"), f"{path}.zone_entity_id", domain="zone"
        )
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    '''    if kind in {"state", "numeric_state", "zone"} and not result.get("entity_id"):
        _error(f"{path}.entity_id", "is required")
    if kind == "state":
        if "state" not in result:
            _error(f"{path}.state", "is required")
        _strict_bool(result, "negate", f"{path}.negate")
''',
    '''    if kind in {"state", "numeric_state", "zone"}:
        result["entity_id"] = _entity_id(result.get("entity_id"), f"{path}.entity_id")
    if kind in {"state", "numeric_state"}:
        _validate_attribute(result, f"{path}.attribute")
    if kind == "state":
        if "state" not in result:
            _error(f"{path}.state", "is required")
        if "attribute" not in result and not isinstance(result["state"], str):
            _error(
                f"{path}.state",
                "must be a string when no attribute is selected",
            )
        _strict_bool(result, "negate", f"{path}.negate")
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    '''    if kind == "zone" and not str(result.get("zone_entity_id", "")).startswith("zone."):
        _error(f"{path}.zone_entity_id", "must be a zone entity")
''',
    '''    if kind == "zone":
        result["zone_entity_id"] = _entity_id(
            result.get("zone_entity_id"), f"{path}.zone_entity_id", domain="zone"
        )
''',
)
replace_once(
    "custom_components/conditional_notifications/validation.py",
    '''    if "notify_entities" in delivery and not isinstance(delivery["notify_entities"], list):
        _error("delivery.notify_entities", "must be a list")
    for entity_id in delivery.get("notify_entities", []):
        if not isinstance(entity_id, str) or not entity_id.startswith("notify."):
            _error("delivery.notify_entities", "may contain only notify entity IDs")
    if "assist_satellites" in delivery and not isinstance(delivery["assist_satellites"], list):
        _error("delivery.assist_satellites", "must be a list")
    for entity_id in delivery.get("assist_satellites", []):
        if not isinstance(entity_id, str) or not entity_id.startswith("assist_satellite."):
            _error("delivery.assist_satellites", "may contain only Assist satellite entity IDs")
''',
    '''    if "notify_entities" in delivery and not isinstance(delivery["notify_entities"], list):
        _error("delivery.notify_entities", "must be a list")
    if "notify_entities" in delivery:
        delivery["notify_entities"] = [
            _entity_id(entity_id, "delivery.notify_entities", domain="notify")
            for entity_id in delivery["notify_entities"]
        ]
    if "assist_satellites" in delivery and not isinstance(delivery["assist_satellites"], list):
        _error("delivery.assist_satellites", "must be a list")
    if "assist_satellites" in delivery:
        delivery["assist_satellites"] = [
            _entity_id(
                entity_id,
                "delivery.assist_satellites",
                domain="assist_satellite",
            )
            for entity_id in delivery["assist_satellites"]
        ]
''',
)
