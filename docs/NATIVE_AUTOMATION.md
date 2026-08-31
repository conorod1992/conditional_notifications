# Native Home Assistant triggers and conditions

Conditional Notifications can store and run Home Assistant-native trigger and condition configuration while retaining its own notification lifecycle semantics.

## Boundary

Home Assistant owns trigger attachment and condition evaluation. Conditional Notifications continues to own correlation (`any` / `all_within`), debounce, cooldown, repeat policy, availability/expiry, resolution, delivery, history, and external named triggers.

Existing legacy Conditional Notifications definitions remain supported and are not migrated on load. The panel converts a legacy trigger or condition only when that part is edited through Home Assistant's native editor.

## Stored shapes

`triggers` may contain either the existing `type:` definitions, including `type: named`, or native Home Assistant trigger objects using `trigger:` (and legacy HA `platform:` input). A Home Assistant `triggers:` group remains one Conditional Notifications correlation slot and uses Home Assistant OR-style group semantics internally.

`conditions` may similarly mix existing `type:` conditions and Home Assistant `condition:` objects. All top-level conditions still use AND semantics.

`resolve_when` accepts either the existing trigger shape or exactly one native Home Assistant trigger.

## Current-state matching

`match_current_state` remains conservative. It is available only for simple state-like definitions whose current truth can be evaluated without inventing semantics for one-shot triggers such as time, webhook, MQTT, or calendar events.

## Permissions

Administrator-owned definitions may use Home Assistant's full trigger and condition set. For non-admin owners, Conditional Notifications only permits native definitions with a clear Home Assistant read-permission boundary (for example state/numeric/zone/time/sun and safe event subscriptions). Trigger/condition types such as templates, MQTT, device automation, webhooks, and other generic integration-defined observers require administrator access rather than bypassing Home Assistant's entity/event permission model.
