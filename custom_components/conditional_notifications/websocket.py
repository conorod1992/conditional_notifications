"""Authenticated owner-aware WebSocket API for the panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .lifecycle import LifecycleNotificationManager
from .manager import AmbiguousReference, DefinitionError, NotFound
from .models import NotificationRecord

WS_TYPE = "conditional_notifications"


def _manager(hass: HomeAssistant) -> LifecycleNotificationManager:
    return hass.data[WS_TYPE]["manager"]


def _send_error(connection: websocket_api.ActiveConnection, msg_id: int, err: Exception) -> None:
    if isinstance(err, AmbiguousReference):
        connection.send_result(msg_id, {"error": "ambiguous", "candidates": err.candidates})
    elif isinstance(err, DefinitionError):
        connection.send_error(msg_id, "invalid_definition", f"{err.field}: {err.message}")
    elif isinstance(err, NotFound):
        connection.send_error(msg_id, "not_found", str(err))
    else:
        connection.send_error(msg_id, "operation_failed", str(err))


def _resolve(
    manager: LifecycleNotificationManager,
    connection: websocket_api.ActiveConnection,
    reference: str,
) -> NotificationRecord:
    return manager.resolve(reference, connection.user.id, connection.user.is_admin)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_TYPE}/list", vol.Optional("query"): cv.string}
)
@websocket_api.async_response
async def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    connection.send_result(
        msg["id"],
        _manager(hass).list_records(connection.user.id, connection.user.is_admin, msg.get("query")),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_TYPE}/get", vol.Required("notification_id"): cv.string}
)
@websocket_api.async_response
async def ws_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        connection.send_result(
            msg["id"], _resolve(_manager(hass), connection, msg["notification_id"]).public_dict()
        )
    except Exception as err:
        _send_error(connection, msg["id"], err)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_TYPE}/create", vol.Required("definition"): dict}
)
@websocket_api.async_response
async def ws_create(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        connection.send_result(
            msg["id"], await _manager(hass).async_create(msg["definition"], connection.user.id)
        )
    except Exception as err:
        _send_error(connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_TYPE}/update",
        vol.Required("notification_id"): cv.string,
        vol.Required("changes"): dict,
    }
)
@websocket_api.async_response
async def ws_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        manager = _manager(hass)
        connection.send_result(
            msg["id"],
            await manager.async_update(
                _resolve(manager, connection, msg["notification_id"]), msg["changes"]
            ),
        )
    except Exception as err:
        _send_error(connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_TYPE}/action",
        vol.Required("notification_id"): cv.string,
        vol.Required("action"): vol.In(
            [
                "pause",
                "resume",
                "enable",
                "disable",
                "rearm",
                "delete",
                "test",
                "trigger_now",
                "duplicate",
            ]
        ),
        vol.Optional("name"): cv.string,
    }
)
@websocket_api.async_response
async def ws_action(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        manager = _manager(hass)
        record = _resolve(manager, connection, msg["notification_id"])
        action = msg["action"]
        if action == "pause":
            result = await manager.async_set_paused(record, True)
        elif action == "resume":
            result = await manager.async_set_paused(record, False)
        elif action == "enable":
            result = await manager.async_set_enabled(record, True)
        elif action == "disable":
            result = await manager.async_set_enabled(record, False)
        elif action == "rearm":
            result = await manager.async_rearm(record)
        elif action == "delete":
            result = await manager.async_delete(record)
        elif action == "test":
            result = await manager.async_test(record)
        elif action == "trigger_now":
            result = await manager.async_trigger_now(record)
        else:
            result = await manager.async_duplicate(record, connection.user.id, msg.get("name"))
        connection.send_result(msg["id"], result)
    except Exception as err:
        _send_error(connection, msg["id"], err)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_TYPE}/history", vol.Optional("notification_id"): cv.string}
)
@websocket_api.async_response
async def ws_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    manager = _manager(hass)
    notification_id = msg.get("notification_id")
    if notification_id and notification_id not in manager.store.records:
        try:
            notification_id = _resolve(manager, connection, notification_id).id
        except NotFound:
            # Retained history for a deleted notification is addressed by its ID.
            pass
        except Exception as err:
            _send_error(connection, msg["id"], err)
            return
    history = manager.history_for_user(
        connection.user.id, connection.user.is_admin, notification_id
    )
    if notification_id and not history:
        current = manager.store.records.get(notification_id)
        if current is not None and not manager.can_access(
            current, connection.user.id, connection.user.is_admin
        ):
            _send_error(connection, msg["id"], NotFound("Notification history was not found"))
            return
    connection.send_result(msg["id"], history)


@websocket_api.websocket_command({vol.Required("type"): f"{WS_TYPE}/preferences"})
@websocket_api.async_response
async def ws_preferences(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    connection.send_result(msg["id"], _manager(hass).options)


@websocket_api.websocket_command({vol.Required("type"): f"{WS_TYPE}/subscribe"})
@websocket_api.async_response
async def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    manager = _manager(hass)

    def forward(payload: dict[str, Any]) -> None:
        record = manager.store.records.get(payload["notification_id"])
        if (
            connection.user.is_admin
            or (record is not None and manager.can_access(record, connection.user.id, False))
            or (record is None and payload.get("owner_id") in {None, connection.user.id})
        ):
            connection.send_event(msg["id"], payload)

    connection.subscriptions[msg["id"]] = manager.subscribe(forward)
    connection.send_result(msg["id"])


def async_register_websocket(hass: HomeAssistant) -> None:
    for command in (
        ws_list,
        ws_get,
        ws_create,
        ws_update,
        ws_action,
        ws_history,
        ws_preferences,
        ws_subscribe,
    ):
        websocket_api.async_register_command(hass, command)
