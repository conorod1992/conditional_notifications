# Conditional Notifications

**Conditional Notifications** is a Home Assistant custom integration for one simple idea:

> Notify me when something happens.

It owns durable, inspectable watches instead of generating disposable automations. Create one in a friendly sidebar panel, from a Home Assistant action, or through a bounded LLM tool; the integration subscribes directly to Home Assistant state/events and restores those subscriptions after a restart.

## Why this exists

A reminder answers “tell me at 10:00.” A deferred action answers “turn this off later.” A conditional notification answers “tell me the next time the kitchen detects motion.” That last job should not require an automation, helper entity, or cleanup afterwards.

Conditional Notifications provides:

- one-shot, repeating, and limited-count watches;
- state, numeric-state, zone, event, and semantic named triggers;
- multiple triggers with clear **any** semantics;
- bounded state, numeric, zone, and time conditions with AND semantics;
- absolute availability/expiry and recurring local-time windows;
- cooldown, debounce, and minimum-duration controls;
- optional no-event notification at expiry;
- auto-resolution with optional persistent-notification clearing;
- Home Assistant persistent notifications and normal `notify` targets;
- response-capable actions, authenticated WebSockets, and structured LLM tools;
- bounded history and a compact aggregate sensor—never one entity per watch.

## Installation

### HACS

1. In HACS, add `https://github.com/conorod1992/conditional_notifications` as a custom **Integration** repository.
2. Install **Conditional Notifications**.
3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Conditional Notifications**.

### Manual

Copy `custom_components/conditional_notifications` into your Home Assistant configuration's `custom_components` directory, restart, and add the integration from Devices & services. Home Assistant 2026.6 or newer is required.

No YAML configuration is required.

## First run and preferences

Adding the integration creates one local config entry and a **Conditional Notifications** sidebar item. Its options configure:

- whether the panel is shown;
- persistent notification as the default delivery channel;
- optional comma-separated `notify` services such as `notify.mobile_app_conors_phone`;
- history retention days and maximum record count;
- whether rendered title/message content is retained in history.

New watches default to **Use my notification defaults**, so delivery does not need to be selected every time.

## Frontend

The responsive panel has **Active**, **Paused**, **History**, and **Expired** views, live updates, search, counts, status/cooldown/expiry details, quick pause/test/edit/delete actions, and a mobile-friendly editor.

The beginner path needs a name, an entity, and a target state. Add another trigger to say “either”; add conditions or open Advanced only when needed. Before saving, the panel produces a plain-English preview of what is watched, when it is active, its conditions, repeat behavior, delivery, and expiry behavior.

Entity selection uses Home Assistant's entity picker. The editor validates obvious issues before sending the definition to the backend; authoritative backend validation always runs as well.

## Examples

### Next kitchen motion

```yaml
name: Next kitchen motion
triggers:
  - type: state
    entity_id: binary_sensor.kitchen_motion
    to: "on"
title: Kitchen motion
message: "{{ trigger.friendly_name }} detected motion at {{ trigger.timestamp }}."
repeat_policy: once
```

### Study or bedroom motion until Sunday

```yaml
name: Upstairs motion this weekend
match: any
triggers:
  - type: state
    entity_id: binary_sensor.study_motion
    to: "on"
  - type: state
    entity_id: binary_sensor.bedroom_motion
    to: "on"
expires_at: "2026-08-16T18:00:00+01:00"
title: Upstairs motion
message: "{{ trigger.friendly_name }} detected motion."
repeat_policy: once
```

### Front door while away

```yaml
name: Front door while away
triggers:
  - type: state
    entity_id: binary_sensor.front_door
    to: "on"
conditions:
  - type: state
    entity_id: person.conor
    state: not_home
title: Front door opened
message: The front door opened while you were away.
repeat_policy: every
cooldown: 1200
```

### Freezer alert that resolves

```yaml
name: Freezer too warm
triggers:
  - type: numeric_state
    entity_id: sensor.freezer_temperature
    above: -10
resolve_when:
  type: numeric_state
  entity_id: sensor.freezer_temperature
  below: -12
title: Freezer is too warm
message: "The freezer is {{ trigger.value }}°C."
repeat_policy: every
clear_on_resolve: true
```

### Nothing happened before 10:00

Set a normal qualifying trigger, an offset-aware `expires_at`, and:

```yaml
notify_on_expiry: true
expiry_title: Kitchen check
expiry_message: No kitchen motion was detected before 10:00.
```

“No event” means no occurrence passed the trigger, timing, conditions, debounce, and cooldown checks. Silent expiry is the default. Any qualifying occurrence prevents a later no-event notification for that watch.

## Trigger types

- **State**: a genuine transition matching optional `from`, `to`, and `attribute`. `for` requires the resulting match to persist. `unknown` and `unavailable` do not match.
- **Numeric state**: entering a strict `above`/`below` range. Remaining inside does not repeatedly trigger. One or both bounds may be supplied.
- **Zone**: an entity enters or leaves a Home Assistant zone.
- **Event**: a named HA event with safe subset matching of optional `event_data`; extra fired-event fields are allowed.
- **Named**: an integration-owned `trigger_id`, fired only with `conditional_notifications.fire_named_trigger`.

Multiple triggers use `match: any`. Arbitrary nested Boolean logic and arbitrary action sequences are intentionally outside this integration.

`match_current_state` is opt-in and evaluated only when a new watch is first created. Normal state triggers require a transition, and restoring listeners after a restart does not treat the restart/current state as an occurrence.

## Conditions

Conditions run after a trigger wakes the watch and all must pass. Supported types are state (with optional `negate`), numeric state, zone, and local time/window. They are deliberately bounded: there is no arbitrary condition template or service execution.

## Repeating and timing controls

- **Once**: accept the first qualifying occurrence, durably count it, then stop.
- **Every trigger**: re-arm after each distinct match.
- **Limited**: stop after exactly `max_notifications`; the durable remaining count survives restart.
- **Cooldown** = minimum time after an accepted notification before another is allowed.
- **Debounce** = collapse/ignore rapid repeated trigger changes within the configured period.
- **Minimum duration** = require the triggering state/range to persist before it qualifies. It uses a cancellable exact timer, never a sleeping task.

`available_from` and `expires_at` must be offset-aware ISO datetimes. Home Assistant's configured timezone drives local recurring windows. An overnight window such as 22:00–07:00 treats the after-midnight segment as belonging to the previous start weekday; absolute availability, expiry, and recurring windows must all allow a trigger.

## Templates

Title, message, expiry content, and optional resolution content are rendered only for the relevant occurrence. They receive normal Home Assistant template helpers plus a friendly `trigger` mapping.

Common fields:

- all: `type`, `trigger_index`, `timestamp`;
- state: `entity_id`, `friendly_name`, `from_state`, `to_state`, `attribute`;
- numeric: `value`, `previous_value`, `above`, `below`, `attribute`;
- event: `event_type`, `event_data`;
- zone: `entity_id`, `zone_entity_id`, `zone`, `event`;
- named: `trigger_id`, `event_data`.

The `notification` mapping contains the current structured status. Syntax is validated on save. A render failure is recorded for that occurrence and does not stop the manager.

## Delivery

The reliable default is an integration-owned Home Assistant persistent notification. A watch may use global defaults or explicitly choose persistent delivery and one or more `notify.*` services. Provider calls are isolated: one failure cannot stop other channels or other watches. Delivery test uses the same rendering/providers but does not alter count or occurrence history.

When auto-resolution is enabled, `clear_on_resolve` dismisses only the tagged persistent notification created by this integration. The integration does not claim it can retract an arbitrary delivered phone notification.

## Home Assistant actions

All actions are under `conditional_notifications` and return JSON-serializable response data:

`create`, `get`, `list`, `update`, `delete`, `pause`, `resume`, `enable`, `disable`, `duplicate`, `test`, `trigger_now`, `fire_named_trigger`, and `clear_history`.

Create/update accept the same bounded definition used by the panel. Existing records resolve by immutable ID, semantic key, or exact name. Ambiguous mutation requests return candidates rather than guessing. `trigger_now` is diagnostic and still passes normal timing, conditions, debounce, cooldown, and repeat checks; `test` tests delivery only.

## Voice and LLM tools

The integration registers the **Conditional Notifications** Home Assistant LLM API. Select it in a compatible conversation agent's LLM API options. It exposes structured tools to create, list, inspect, update, pause/resume/enable/disable, duplicate, test, and delete.

The system prompt tells agents to use entity IDs and bounded definitions, never automation YAML. Tools cannot call arbitrary services or submit arbitrary Home Assistant actions. Exact IDs/semantic keys/names are preferred; if a reference is ambiguous, the tool returns safe candidates for clarification.

## Persistence, races, and restart behavior

Definitions, enabled/paused/active state, qualifying-match flag, counts, cooldown acceptance time, last useful status, and bounded history live in Home Assistant's versioned storage. Listeners and timers are runtime-only and reconstructed on config-entry setup.

Each record has a lock and monotonically increasing revision. Editing/deleting invalidates its previous listeners and pending duration callback. Trigger acceptance and occurrence count are saved before external delivery, preventing a provider failure or crash from creating an uncontrolled retry loop. Concurrent callbacks re-check revision/status inside the lock.

Minimum-duration timers are conservative after restart: elapsed pre-restart time is not assumed proven, so a restart never fabricates a duration occurrence. Exact future activation/expiry timers are reconstructed from their durable timestamps. Cooldown, limited counts, active resolution state, and no-event satisfaction survive restart.

## Status and history

Structured status includes temporal eligibility, status, pause/enable state, expiry, next cooldown eligibility, count/remaining count, last trigger, last bounded ignore reason, active occurrence, and last channel results. Meaningful history includes create/update, match, condition rejection, delivery/template failure, resolve, pause/resume, expiry, and no-event delivery.

Retention is bounded by both age and maximum record count. The optional summary entity `sensor.conditional_notifications_active` exposes only small aggregate counts and the latest trigger summary; definitions are never placed in entity attributes or Recorder rows.

## Security

Ownership uses immutable Home Assistant user IDs. Browser and LLM requests derive identity from authenticated backend context; they cannot supply their own owner. Normal users see and mutate only their records. Administrators may manage all records. Internal service calls without a user context create shared/system-owned records. Diagnostics contain aggregate counts and preferences only—never rendered private notification content, tokens, webhook IDs, or full definitions.

## Troubleshooting

- **It did not fire:** inspect the detail/status view for active period, condition result, cooldown, last ignored reason, and delivery result.
- **It was already on:** state triggers require a transition. Opt into **match current state immediately** only when that is intended.
- **A door did not satisfy `for`:** any nonmatching transition cancels the duration timer. Restarts conservatively reset unproven elapsed duration.
- **Phone delivery failed:** verify the exact `notify.*` service in Developer Tools → Actions and keep persistent notification enabled as a fallback.
- **Panel missing:** enable it in the integration options and reload the config entry.
- **Templates fail:** test the expression in Developer Tools → Template and use the documented friendly trigger fields.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff format --check .
ruff check .
mypy custom_components/conditional_notifications
pytest
cd frontend
npm run lint
npm test
npm run build
```

CI also runs HACS and hassfest validation. The committed panel is a dependency-free ES module and the build is idempotent; CI fails if building changes it.

## License

MIT
