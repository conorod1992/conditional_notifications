# Conditional Notifications

**Conditional Notifications** is a custom integration for Home Assistant that lets you create notifications which wait for something to happen.

For example:

- notify me the next time the kitchen detects motion;
- tell me if the front door opens while I am away;
- alert me when the freezer becomes too warm;
- notify me if **nothing** happens before a deadline;
- keep notifying me when something happens, with an optional cooldown between alerts.

You create and manage these from a **Conditional Notifications** panel in the Home Assistant sidebar. No YAML configuration is required.

> A normal reminder says **“tell me at 10:00.”**  
> A conditional notification says **“tell me when this happens.”**

## What can it do?

A conditional notification can watch for:

- an entity changing state;
- a sensor entering a numeric range;
- a person or device entering or leaving a zone;
- a Home Assistant event;
- a named trigger fired by another automation, script, integration, or action.

You can also add:

- multiple triggers — notify when **any** happens, or require **all** of them within a chosen time window;
- conditions — only notify if **all** conditions are true;
- start and expiry times;
- recurring time windows;
- cooldowns and debounce;
- a minimum duration, such as “only if the door stays open for 5 minutes”;
- one-off, repeating, or limited-count notifications;
- an optional notification when the watch expires without anything happening;
- automatic resolution when the problem clears.

Notifications can be delivered as Home Assistant persistent notifications, through `notify` entities such as the Companion App, or both.

## Installation

### HACS

If you already use HACS:

1. Open **HACS** in Home Assistant.
2. Select the **three-dot menu** in the top-right corner.
3. Choose **Custom repositories**.
4. Add:

   ```text
   https://github.com/conorod1992/conditional_notifications
   ```

5. Select **Integration** as the repository type.
6. Select **Add**.
7. Find **Conditional Notifications** in HACS and download it.
8. Restart Home Assistant.
9. Go to **Settings → Devices & services**.
10. Select **Add integration**, search for **Conditional Notifications**, and add it.

Home Assistant **2026.6.0 or newer** is required.

> New to custom HACS repositories? Adding a custom repository simply tells HACS where to find an integration which is not in its normal catalogue. HACS still handles downloading and updating it for you.

### Manual installation

1. Download or clone this repository.
2. Copy the folder:

   ```text
   custom_components/conditional_notifications
   ```

   into the `custom_components` folder inside your Home Assistant configuration directory.

3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**.
5. Search for **Conditional Notifications** and add it.

No YAML configuration is required.

## Getting started

After adding the integration, a **Conditional Notifications** item appears in the Home Assistant sidebar.

The panel contains:

- **Active** — notifications currently waiting for something to happen;
- **Paused** — notifications you have temporarily stopped;
- **History** — previous activity and notification results;
- **Expired** — watches which reached their expiry time.

### Create your first conditional notification

A simple state-based notification only needs a few things:

1. Select **Create** in the Conditional Notifications panel.
2. Give it a name.
3. Choose an entity.
4. Choose the state you want to watch for.
5. Enter the notification title and message.
6. Save it.

For example:

> **Name:** Next kitchen motion  
> **Entity:** Kitchen motion sensor  
> **State:** On  
> **Message:** Motion was detected in the kitchen.

The panel shows a plain-English summary before you save, so you can check what the notification will watch for.

You do not need to configure the advanced options unless you need them.

## A few useful examples

### Notify me the next time motion is detected

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

This triggers once, then stops.

### Notify me if either of two motion sensors activates

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
expires_at: "2026-08-30T18:00:00+01:00"
title: Upstairs motion
message: "{{ trigger.friendly_name }} detected motion."
repeat_policy: once
```

With `match: any`, **any one** of the triggers can satisfy the notification.

### Notify me when several signals happen close together

```yaml
name: Front door activity
match: all_within
match_window: 30
triggers:
  - type: state
    entity_id: binary_sensor.front_door
    to: "on"
  - type: state
    entity_id: binary_sensor.hall_motion
    to: "on"
title: Front door activity
message: The door and hall motion were both detected within 30 seconds.
repeat_policy: every
```

With `match: all_within`, every configured trigger must occur within the chosen window. The order does not matter. If one signal becomes too old before the others occur, it is discarded from the current correlation window.

### Notify me when the front door opens, but only while I am away

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

This can notify repeatedly, but the 1200-second cooldown prevents another accepted notification for 20 minutes.

### Notify me if the freezer becomes too warm

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

This also defines a resolution condition. When the temperature falls below `-12`, the active problem is considered resolved.

If the notification was delivered as a persistent Home Assistant notification, `clear_on_resolve: true` can dismiss that persistent notification automatically.

### Notify me if nothing happened before a deadline

Create a normal trigger, set an expiry time, then enable **Notify on expiry**.

Equivalent definition:

```yaml
notify_on_expiry: true
expiry_title: Kitchen check
expiry_message: No kitchen motion was detected before 10:00.
```

A “no event” notification is sent only if no qualifying occurrence was accepted before the watch expired.

## Trigger types

### State

Watches an entity for a real state change.

Examples include:

- a binary sensor changing to `on`;
- a light changing to `off`;
- a person changing to `not_home`;
- an attribute changing to a particular value.

A normal state trigger waits for a **transition**. If the entity is already in the target state when you create the notification, it does not immediately count as a new occurrence.

If you specifically want the current state to be checked when creating a new notification, enable **Match current state immediately**.

`unknown` and `unavailable` do not count as matching states.

### Numeric state

Watches a numeric sensor for entry into a range.

For example:

- temperature rises above 25;
- battery level falls below 20;
- humidity moves between two limits.

One or both of `above` and `below` can be used.

The notification triggers when the value **enters** the matching range, rather than repeatedly firing while it remains there.

### Zone

Watches for an entity entering or leaving a Home Assistant zone.

For example:

- a person arrives home;
- a device leaves the home zone;
- a person enters a work zone.

### Event

Watches for a Home Assistant event.

You can optionally match selected fields from the event data. The fired event may contain additional fields without preventing a match.

This is mainly useful for more advanced Home Assistant setups or for events fired by another integration.

### Named trigger

A named trigger is an integration-owned trigger ID which you can fire using:

```text
conditional_notifications.fire_named_trigger
```

This is useful when another automation, script, or integration should signal a conditional notification without tying it directly to an entity state.

## Conditions

Conditions are optional extra checks.

A trigger must happen first, then **all** configured conditions must pass before the notification is accepted.

Supported conditions are:

- state;
- numeric state;
- zone;
- local time or time window.

For example:

> Notify me when the front door opens **only if I am away**.

The front-door state change is the trigger. Your presence state is the condition.

Conditional Notifications intentionally supports a defined set of conditions rather than arbitrary Home Assistant action sequences or unrestricted templates.

## Repeating notifications

You can choose how often a notification may be accepted.

### Once

Notify on the first qualifying occurrence, then stop.

### Every trigger

Continue watching and allow future qualifying occurrences.

### Limited

Allow a fixed number of accepted notifications, then stop.

The remaining count is saved and survives a Home Assistant restart.

## Timing controls

### Cooldown

A cooldown sets the minimum time after an accepted notification before another one may be accepted.

Example:

> Notify me every time the door opens, but no more than once every 20 minutes.

### Debounce

Debounce helps ignore or collapse rapid repeated changes which happen close together.

This can be useful for noisy sensors or events which may fire several times in quick succession.

### Minimum duration

A trigger can be required to remain true for a minimum time before it qualifies.

Example:

> Notify me if the garage door stays open for 5 minutes.

If the state stops matching before the duration is reached, the pending occurrence is cancelled.

### Availability and expiry

You can limit when a notification is active using:

- `available_from` — do not accept occurrences before this time;
- `expires_at` — stop watching after this time;
- recurring local-time windows.

Dates and times entered directly in definitions must include their timezone offset, for example:

```text
2026-08-30T18:00:00+01:00
```

The panel handles normal user-facing date and time entry for you.

Recurring windows use the timezone configured in Home Assistant.

Overnight windows are supported. For example, a window from 22:00 to 07:00 continues through midnight into the following morning.

## Notification delivery

By default, Conditional Notifications can use a Home Assistant persistent notification.

You can change the integration-wide defaults under:

**Settings → Devices & services → Conditional Notifications → Configure**

You can configure:

- whether the sidebar panel is shown;
- whether persistent notifications are used by default;
- default `notify` targets;
- history retention;
- whether rendered notification titles and messages are retained in history.

New notifications default to **Use my notification defaults**, so you do not need to choose the same delivery method every time.

Individual notifications can instead choose their own delivery targets.

Modern Home Assistant `notify` entities are supported using `notify.send_message`. Previously stored legacy `notify.*` service names remain supported for compatibility.

If more than one delivery channel is configured, failure of one channel does not prevent the others from being attempted.

### Companion App options

For Home Assistant Companion App notify targets, an individual conditional notification can optionally add:

- a destination to open when the notification itself is tapped;
- up to three action buttons;
- buttons which either open a link or fire a named Companion App notification-action event that another Home Assistant automation can handle.

Home Assistant-relative destinations must begin with a single `/`, such as `/lovelace/security`. Full `http://` and `https://` URLs are also accepted.

These options are deliberately bounded. Conditional Notifications does **not** expose an arbitrary Companion App `data` object, arbitrary service calls, command URIs, or unrestricted notification payload fields.

If a Companion-specific payload is sent to a notify target which does not support it, that target may report a delivery failure; other configured delivery channels are still attempted independently.

### Test delivery

The **Test** option renders and sends the notification using the real configured delivery channels.

A test does **not**:

- increase the notification count;
- satisfy a trigger;
- add a normal occurrence to the watch history.

## Automatic resolution

Some notifications represent a temporary problem rather than a single event.

For example:

> Alert me when the freezer is above -10 °C, and consider the problem resolved when it falls below -12 °C.

Use `resolve_when` to define the resolution condition.

If `clear_on_resolve` is enabled, Conditional Notifications can dismiss the persistent Home Assistant notification that it created for that occurrence.

It cannot retract a phone push notification which has already been delivered by another provider.

## Templates

Notification titles and messages can use Home Assistant templates.

For example:

```yaml
message: "{{ trigger.friendly_name }} detected motion at {{ trigger.timestamp }}."
```

Conditional Notifications provides a `trigger` object containing useful information about the occurrence.

Common fields include:

### All trigger types

- `type`
- `trigger_index`
- `timestamp`

### State triggers

- `entity_id`
- `friendly_name`
- `from_state`
- `to_state`
- `attribute`

### Numeric-state triggers

- `value`
- `previous_value`
- `above`
- `below`
- `attribute`

### Event triggers

- `event_type`
- `event_data`

### Zone triggers

- `entity_id`
- `zone_entity_id`
- `zone`
- `event`

### Named triggers

- `trigger_id`
- `event_data`

For a completed `all_within` correlation, the trigger object also contains `matched_triggers` with the individual trigger contexts and `correlation` with the configured window and first/last match times.

A `notification` object is also available with the notification's current structured status.

Template syntax is checked when the notification is saved. If rendering later fails for a particular occurrence, the failure is recorded without stopping the rest of the integration.

## Home Assistant actions

Conditional Notifications provides Home Assistant actions under the `conditional_notifications` domain:

- `create`
- `get`
- `list`
- `update`
- `delete`
- `pause`
- `resume`
- `enable`
- `disable`
- `duplicate`
- `test`
- `trigger_now`
- `fire_named_trigger`
- `clear_history`

These are useful when another automation, script, integration, or advanced workflow needs to manage conditional notifications.

Create and update use the same definition format as the sidebar panel.

Existing notifications can be referenced by their ID, semantic key, or exact name. If a reference could mean more than one notification, the integration returns possible matches rather than guessing.

### `test` versus `trigger_now`

These are deliberately different:

- `test` only tests rendering and delivery;
- `trigger_now` submits a manual occurrence through the normal timing, condition, debounce, cooldown, and repeat checks.

## Voice and LLM tools

Conditional Notifications also registers a Home Assistant LLM API.

If you use a compatible Home Assistant conversation agent, you can select the **Conditional Notifications** LLM API in that agent's LLM API options.

It provides structured tools which can create, inspect, list, update, pause, resume, enable, disable, duplicate, test, and delete conditional notifications.

The LLM tools are deliberately limited to Conditional Notifications definitions. They cannot use this integration as a way to run arbitrary Home Assistant services or arbitrary action sequences.

If you do not use an LLM or voice assistant, you can ignore this section completely.

## What happens after a restart?

Your notification definitions and important progress are stored by the integration.

After Home Assistant restarts, Conditional Notifications recreates the listeners and timers it needs and continues watching.

Saved information includes items such as:

- whether a notification is enabled or paused;
- accepted-notification counts;
- limited-notification remaining counts;
- cooldown timing;
- active resolution state;
- expiry state;
- whether a qualifying occurrence has already happened for no-event handling;
- bounded history.

For minimum-duration checks, the integration takes a conservative approach after a restart: time which elapsed before the restart is not assumed to have been continuously proven. This prevents a restart from creating a false duration-based occurrence.

Partial `all_within` correlations are also deliberately cleared on restart or listener rebuild. A complete fresh set of matching triggers must occur after listeners are active again, so events on opposite sides of downtime are never joined together.

## Status and history

The detail view can show information such as:

- whether the notification is currently eligible to fire;
- whether it is active, paused, disabled, or expired;
- expiry time;
- next cooldown eligibility;
- notification count and remaining count;
- last trigger;
- most recent reason an occurrence was ignored;
- active resolution state;
- delivery results.

History may include events such as:

- creation and editing;
- trigger matches;
- condition rejection;
- notification delivery;
- template or delivery failures;
- resolution;
- pause and resume;
- expiry;
- no-event notification delivery.

History is automatically bounded by both age and a maximum record count.

The optional `sensor.conditional_notifications_active` entity contains only small summary information. Conditional notification definitions are not stored in that entity's attributes or written into Recorder rows.

## Multiple users and security

Conditional Notifications uses Home Assistant's authenticated user identity.

Normal users can see and manage only their own conditional notifications. Home Assistant administrators may manage all records.

Browser and LLM requests use the authenticated Home Assistant user; they cannot simply provide a different owner ID.

Actions called internally without a Home Assistant user context create shared/system-owned records.

Diagnostics contain only limited aggregate information and preferences. They do not include full conditional-notification definitions, notification content, tokens, or webhook IDs.

## Troubleshooting

### It did not notify me

Open the notification's detail/status view and check:

- whether it is currently active;
- its availability or expiry;
- condition results;
- cooldown;
- the last ignored reason;
- delivery results.

### The entity was already in the target state

Normal state triggers wait for a **change into** the target state.

If you want a newly created notification to check the entity's current state immediately, enable **Match current state immediately**.

### A `for` / minimum-duration trigger did not complete

Any transition which stops matching cancels the pending duration check.

A Home Assistant restart also conservatively resets unproven elapsed duration.

### Phone delivery failed

Check the selected `notify` entity or service in **Developer Tools → Actions**.

If desired, keep Home Assistant persistent notifications enabled as an additional delivery channel.

### The sidebar panel is missing

Go to:

**Settings → Devices & services → Conditional Notifications → Configure**

and make sure the panel is enabled.

Reload the integration if necessary.

### A template failed

Test the template in **Developer Tools → Template** and use the supported `trigger` fields documented above.

## Technical design notes

The following details are mainly relevant to developers, contributors, or anyone interested in how the integration avoids duplicate or unsafe behaviour.

Conditional Notifications stores its records in Home Assistant's versioned storage. Runtime listeners and timers are reconstructed when the config entry is loaded.

Each notification record has its own lock and revision number. Editing or deleting a record invalidates its previous listeners and pending duration callback.

An accepted occurrence and its count are saved before notification providers are called. This prevents a provider failure or crash from causing an uncontrolled retry loop.

Concurrent callbacks re-check the current record revision and status before accepting an occurrence.

The integration deliberately supports a bounded set of trigger and condition definitions rather than arbitrary action execution.

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

CI also runs HACS and hassfest validation.

The committed frontend panel is a dependency-free ES module and the build is expected to be idempotent; CI fails if rebuilding changes the committed output.

## License

MIT