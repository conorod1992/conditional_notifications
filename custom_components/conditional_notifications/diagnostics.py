"""Privacy-conscious config-entry diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    manager = entry.runtime_data
    records = list(manager.store.records.values())
    return {
        "version": 1,
        "record_count": len(records),
        "history_count": len(manager.store.history),
        "status_counts": {
            status: sum(1 for record in records if record.status == status)
            for status in {record.status for record in records}
        },
        "trigger_type_counts": {
            kind: sum(
                1
                for record in records
                for trigger in record.definition["triggers"]
                if trigger["type"] == kind
            )
            for kind in {
                trigger["type"] for record in records for trigger in record.definition["triggers"]
            }
        },
        "options": {
            "panel_enabled": manager.options["panel_enabled"],
            "history_retention_days": manager.options["history_retention_days"],
            "history_max_records": manager.options["history_max_records"],
            "retain_content": manager.options["retain_content"],
        },
    }
