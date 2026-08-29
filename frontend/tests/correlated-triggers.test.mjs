import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
globalThis.customElements = {get: () => undefined, define: () => {}};
const { ConditionalNotificationsPanel } = await import("../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-correlation.js");
const panel = ConditionalNotificationsPanel.prototype;

const definition = {
  name:"Departure",
  triggers:[
    {type:"state",entity_id:"binary_sensor.front_door",to:"on"},
    {type:"state",entity_id:"binary_sensor.hall_motion",to:"on"},
  ],
  match:"all_within",
  match_window:30,
  conditions:[],
  title:"Departure",
  message:"Detected",
  repeat_policy:"every",
  delivery:{use_defaults:true},
  notify_on_expiry:false,
};

test("preview explains correlated matching", () => {
  const context = {
    triggerSummary:panel.triggerSummary,
    conditionSummary:panel.conditionSummary,
    duration:panel.duration,
  };
  const preview = panel.preview.call(context, definition);
  assert.match(preview.watching,/All configured triggers within 30 seconds/);
  assert.match(preview.watching,/Front Door/);
  assert.match(preview.watching,/Hall Motion/);
});

test("preview summarizes bounded Companion App options", () => {
  const context = {
    triggerSummary:panel.triggerSummary,
    conditionSummary:panel.conditionSummary,
    duration:panel.duration,
  };
  const value = structuredClone(definition);
  value.delivery.companion = {
    url:"/lovelace/security",
    actions:[{title:"Acknowledge",action:"ACK_ALERT"}],
  };
  const preview = panel.preview.call(context, value);
  assert.match(preview.delivery,/tap opens \/lovelace\/security/);
  assert.match(preview.delivery,/1 action button/);
});

test("editor helpers render correlation and Companion App controls", () => {
  const context = {errors:{}};
  const correlation = panel.renderCorrelationOptions.call(context, definition);
  const companion = panel.renderCompanionOptions.call(context, definition);
  assert.match(correlation,/All triggers within a time window/);
  assert.match(correlation,/data-path="match_window"/);
  assert.match(correlation,/value="30"/);
  assert.match(companion,/Companion App extras/);
  assert.match(companion,/id="companion-url"/);
  assert.match(companion,/Add action button/);
});

test("custom delivery renders distinct notify and Assist satellite targets", () => {
  const value = structuredClone(definition);
  value.delivery = {
    use_defaults:false,
    persistent_notification:false,
    notify_entities:["notify.conors_phone"],
    assist_satellites:["assist_satellite.kitchen"],
  };
  const markup = panel.renderCustomDelivery.call({errors:{}}, value);
  assert.match(markup,/Phones & notification devices/);
  assert.match(markup,/data-domain="notify"/);
  assert.match(markup,/Voice announcements/);
  assert.match(markup,/assist_satellite\.announce/);
  assert.match(markup,/data-domain="assist_satellite"/);
});
