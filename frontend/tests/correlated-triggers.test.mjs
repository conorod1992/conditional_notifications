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

test("correlation editor renders mode and window controls", () => {
  let inserted = "";
  const anchor = {insertAdjacentHTML: (_where, markup) => { inserted = markup; }};
  const context = {
    editor:{definition:structuredClone(definition)},
    shadowRoot:{
      querySelector(selector) {
        if (selector === "#trigger-match-mode") return null;
        if (selector === ".preview") return anchor;
        if (selector === '[data-path="delivery.use_defaults"]') return null;
        if (selector === "#notify-services") return null;
        if (selector === '[data-path="resolve_when.for"]') return null;
        return null;
      },
    },
  };
  panel.hydrateEditor.call(context);
  assert.match(inserted,/All triggers within a time window/);
  assert.match(inserted,/data-path="match_window"/);
  assert.match(inserted,/value="30"/);
});
