"""UI-only setup and preferences flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import DEFAULT_OPTIONS, DOMAIN, NAME


class ConditionalNotificationsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Create the single local entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=DEFAULT_OPTIONS)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ConditionalNotificationsOptionsFlow:
        return ConditionalNotificationsOptionsFlow()


class ConditionalNotificationsOptionsFlow(config_entries.OptionsFlow):
    """Edit safe integration-wide defaults."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        options = {**DEFAULT_OPTIONS, **self.config_entry.options}
        if user_input is not None:
            delivery = dict(options["delivery"])
            delivery["persistent_notification"] = user_input.pop("persistent_notification")
            services = user_input.pop("notify_services", "")
            delivery["notify_services"] = [
                item.strip() for item in services.split(",") if item.strip()
            ]
            return self.async_create_entry(
                title="", data={**options, **user_input, "delivery": delivery}
            )
        schema = vol.Schema(
            {
                vol.Required("panel_enabled", default=options["panel_enabled"]): BooleanSelector(),
                vol.Required(
                    "persistent_notification",
                    default=options["delivery"].get("persistent_notification", True),
                ): BooleanSelector(),
                vol.Optional(
                    "notify_services",
                    default=", ".join(options["delivery"].get("notify_services", [])),
                ): str,
                vol.Required(
                    "history_retention_days", default=options["history_retention_days"]
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=365, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    "history_max_records", default=options["history_max_records"]
                ): NumberSelector(
                    NumberSelectorConfig(min=50, max=5000, step=50, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    "retain_content", default=options["retain_content"]
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
