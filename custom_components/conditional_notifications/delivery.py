"""Isolated native delivery channels and template rendering."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.template import Template

from .const import DOMAIN
from .models import NotificationRecord, parse_datetime

DELIVERY_TIMEOUT_SECONDS = 30


async def async_render(
    hass: HomeAssistant, source: str, trigger: dict[str, Any], record: NotificationRecord
) -> str:
    template = Template(source, hass)
    friendly_trigger = dict(trigger)
    if isinstance(friendly_trigger.get("timestamp"), str):
        friendly_trigger["timestamp"] = parse_datetime(friendly_trigger["timestamp"])
    return str(
        template.async_render(
            {"trigger": friendly_trigger, "notification": record.public_dict()},
            parse_result=False,
        )
    )


def merge_delivery(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge delivery targets while retaining per-notification Companion options."""
    if override.get("use_defaults", True):
        delivery = deepcopy(defaults)
    else:
        delivery = {
            "persistent_notification": bool(override.get("persistent_notification")),
            "notify_entities": list(override.get("notify_entities", [])),
            "notify_services": list(override.get("notify_services", [])),
            "assist_satellites": list(override.get("assist_satellites", [])),
        }
    if companion := override.get("companion"):
        delivery["companion"] = deepcopy(companion)
    return delivery


def _notify_payload(title: str, message: str, delivery: dict[str, Any]) -> dict[str, Any]:
    """Build the legacy notify-service payload, including bounded Companion data."""
    payload: dict[str, Any] = {"title": title, "message": message}
    companion = delivery.get("companion")
    if not companion:
        return payload

    data: dict[str, Any] = {}
    if url := companion.get("url"):
        data["url"] = url
    if actions := companion.get("actions"):
        data["actions"] = [
            {"action": "URI", "title": item["title"], "uri": item["uri"]}
            if item.get("uri")
            else {"action": item["action"], "title": item["title"]}
            for item in actions
        ]
    if data:
        payload["data"] = data
    return payload


async def _async_service_call(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    context: Context | None = None,
) -> None:
    """Bound one provider call so a stuck target cannot wedge a delivery forever."""
    kwargs: dict[str, Any] = {"blocking": True}
    if target is not None:
        kwargs["target"] = target
    if context is not None:
        kwargs["context"] = context
    async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):
        await hass.services.async_call(domain, service, data, **kwargs)


async def _delivery_identity(
    hass: HomeAssistant, record: NotificationRecord
) -> tuple[Context | None, bool]:
    owner_id = getattr(record, "owner_id", None)
    if owner_id is None:
        return None, True
    user = await hass.auth.async_get_user(owner_id)
    return Context(user_id=owner_id), bool(user and user.is_admin)


def _error_text(err: Exception) -> str:
    if isinstance(err, TimeoutError):
        return f"Timed out after {DELIVERY_TIMEOUT_SECONDS} seconds"
    return str(err)[:300]


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
    context, owner_is_admin = await _delivery_identity(hass, record)
    entity_payload = {"title": title, "message": message}
    service_payload = _notify_payload(title, message, delivery)
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
                {
                    "channel": "persistent_notification",
                    "success": False,
                    "error": _error_text(err),
                }
            )
    for entity_id in delivery.get("notify_entities", []):
        try:
            # Home Assistant's notify.send_message entity service intentionally
            # exposes only message/title. Extended Companion App data belongs to
            # legacy notify.mobile_app_* services below.
            await _async_service_call(
                hass,
                "notify",
                "send_message",
                entity_payload,
                target={"entity_id": entity_id},
                context=context,
            )
            results.append({"channel": entity_id, "success": True})
        except Exception as err:
            results.append({"channel": entity_id, "success": False, "error": _error_text(err)})
    for entity_id in delivery.get("assist_satellites", []):
        try:
            # Assist satellites are an announcement channel rather than notify
            # entities. Speak the message only; the visual title is intentionally
            # not repeated aloud.
            await _async_service_call(
                hass,
                "assist_satellite",
                "announce",
                {"message": message},
                target={"entity_id": entity_id},
                context=context,
            )
            results.append({"channel": entity_id, "success": True})
        except Exception as err:
            results.append({"channel": entity_id, "success": False, "error": _error_text(err)})
    for service in delivery.get("notify_services", []):
        if context is not None and not owner_is_admin:
            results.append(
                {
                    "channel": service,
                    "success": False,
                    "error": "Legacy notify services require an administrator-owned notification",
                }
            )
            continue
        try:
            domain, service_name = service.split(".", 1) if "." in service else ("notify", service)
            if domain != "notify":
                raise ValueError("only notify services are allowed")
            await _async_service_call(
                hass,
                "notify",
                service_name,
                service_payload,
                context=context,
            )
            results.append({"channel": f"notify.{service_name}", "success": True})
        except Exception as err:
            results.append({"channel": service, "success": False, "error": _error_text(err)})
    if not results:
        results.append(
            {"channel": "none", "success": False, "error": "No delivery channel configured"}
        )
    return results


def async_clear(hass: HomeAssistant, notification_id: str) -> None:
    """Clear only the integration-owned persistent notification tag."""
    persistent_notification.async_dismiss(hass, f"{DOMAIN}_{notification_id}")
