import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
globalThis.customElements = {get: () => undefined, define: () => {}};
const { ConditionalNotificationsPanel } = await import("../../custom_components/conditional_notifications/frontend/conditional-notifications-panel.js");
const panel = ConditionalNotificationsPanel.prototype;

test("duration formatting explains cooldowns", () => {
  assert.equal(panel.duration(1200), "20 minutes");
  assert.equal(panel.duration(5), "5 seconds");
});
test("state trigger summary includes minimum duration", () => {
  assert.equal(
    panel.triggerSummary({type:"state", entity_id:"binary_sensor.back_door", to:"on", for:300}),
    "Back Door changes to on and stays there for 5 minutes",
  );
});

test("plain English preview distinguishes once, cooldown, and expiry", () => {
  const context = {triggerSummary:panel.triggerSummary, duration:panel.duration};
  const result = panel.preview.call(context, {
    triggers:[{type:"state",entity_id:"binary_sensor.motion",to:"on"}],
    conditions:[],repeat_policy:"every",cooldown:1200,delivery:{use_defaults:true},
    expires_at:"2026-08-16T18:00:00+01:00",notify_on_expiry:false,
  });
  assert.match(result.behaviour, /every distinct match/);
  assert.match(result.behaviour, /20 minutes/);
  assert.equal(result.expiry, "Expire silently");
});
