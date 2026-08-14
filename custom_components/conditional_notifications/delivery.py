"""Isolated native delivery channels and template rendering."""

from __future__ import annotations

from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from .const import DOMAIN
from .models import NotificationRecord, parse_datetime


async def async_render(
    hass: HomeAssistant, source: str, trigger: dict[str, Any], record: NotificationRecord
) -> str:
    template = Template(source, hass)
    friendly_trigger = dict(trigger)
    if isinstance(friendly_trigger.get("timestamp"), str):
        friendly_trigger["timestamp"] = parse_datetime(friendly_trigger["timestamp"])
    return str(
        template.async_render(
            {"trigger": friendly_trigger, "notification": record.public_dict()}, parse_result=False
        )
    )


def merge_delivery(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    if override.get("use_defaults", True):
        return defaults
    return {
        "persistent_notification": bool(override.get("persistent_notification")),
        "notify_entities": list(override.get("notify_entities", [])),
        "notify_services": list(override.get("notify_services", [])),
    }


async def async_deliver(
    hass: HomeAssistant,
    record: NotificationRecord,
    title: str,
    message: str,
    defaults: dict[str, Any],
    *,
    test: bool = False,
) -> list[dict[str, Any]]:
    """Deliver independently; one channel failure cannot stop another."""
    delivery = merge_delivery(defaults, record.definition.get("delivery", {}))
    results: list[dict[str, Any]] = []
    notification_id = f"{DOMAIN}_{record.id}"
    if test:
        notification_id += "_test"
    if delivery.get("persistent_notification"):
        try:
            persistent_notification.async_create(
                hass, message, title=title, notification_id=notification_id
            )
            results.append({"channel": "persistent_notification", "success": True})
        except Exception as err:
            results.append(
                {"channel": "persistent_notification", "success": False, "error": str(err)[:300]}
            )
    for entity_id in delivery.get("notify_entities", []):
        try:
            await hass.services.async_call(
                "notify",
                "send_message",
                {"title": title, "message": message},
                blocking=True,
                target={"entity_id": entity_id},
            )
            results.append({"channel": entity_id, "success": True})
        except Exception as err:
            results.append({"channel": entity_id, "success": False, "error": str(err)[:300]})
    for service in delivery.get("notify_services", []):
        try:
            domain, service_name = service.split(".", 1) if "." in service else ("notify", service)
            if domain != "notify":
                raise ValueError("only notify services are allowed")
            await hass.services.async_call(
                "notify", service_name, {"title": title, "message": message}, blocking=True
            )
            results.append({"channel": f"notify.{service_name}", "success": True})
        except Exception as err:
            results.append({"channel": service, "success": False, "error": str(err)[:300]})
    if not results:
        results.append(
            {"channel": "none", "success": False, "error": "No delivery channel configured"}
        )
    return results


def async_clear(hass: HomeAssistant, notification_id: str) -> None:
    """Clear only the integration-owned persistent notification tag."""
    persistent_notification.async_dismiss(hass, f"{DOMAIN}_{notification_id}")
