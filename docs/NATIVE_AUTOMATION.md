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


## Duration and rebuild behavior

Static native `for` values, including strings such as `00:05:00`, are normalized for conservative current-state proofs. Dynamic durations remain owned by HA and are never guessed by the integration. Seeded trigger and resolution proofs are cancelled whenever their truth is interrupted. Disabled native fragments are excluded from current-state projection; an entirely disabled group is a valid inactive subscription.

Legacy zone transitions and current-state zone resolution require known location data. Losing location, becoming unavailable, or losing the zone does not prove that an entity left it.

Every runtime owns its subscriptions and proof timers. Cancelling it immediately invalidates callbacks; attachment results arriving after cancellation are unsubscribed immediately. A later build cannot inherit orphaned listeners from an earlier build.

Native observer templates are administrator-only throughout each fragment, including nested groups, condition trees, trigger variables, and durations. Entity registry UUIDs are resolved before entity read permissions are checked. Notification title/message templates for non-admin owners use only trigger/notification data and standard Jinja, without HA globals.
