import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
globalThis.customElements = {get: () => undefined, define: () => {}};
const { ConditionalNotificationsPanel } = await import("../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-status.js");
const panel = ConditionalNotificationsPanel.prototype;

const record = (overrides = {}) => ({
  id:"abc",
  name:"Freezer warning",
  description:"",
  status:"watching",
  enabled:true,
  paused:false,
  currently_active:true,
  notification_count:1,
  remaining_notifications:null,
  next_eligible_at:null,
  active_occurrence:false,
  last_trigger_at:null,
  last_trigger:null,
  last_ignored_reason:null,
  last_delivery:[],
  created_at:"2026-08-27T10:00:00+01:00",
  updated_at:"2026-08-27T10:00:00+01:00",
  revision:1,
  definition:{
    name:"Freezer warning",
    triggers:[{type:"numeric_state",entity_id:"sensor.freezer",above:-10}],
    conditions:[],
    repeat_policy:"every",
    delivery:{use_defaults:true},
    notify_on_expiry:false,
  },
  ...overrides,
});

test("eligibility explains the major runtime states", () => {
  const now = new Date("2026-08-27T12:00:00Z");
  assert.equal(panel.eligibilitySummary(record({status:"expired"}), now).state, "Expired");
  assert.equal(panel.eligibilitySummary(record({enabled:false}), now).state, "Disabled");
  assert.equal(panel.eligibilitySummary(record({paused:true}), now).state, "Paused");
  assert.equal(panel.eligibilitySummary(record({currently_active:false}), now).state, "Outside active period");
  assert.equal(panel.eligibilitySummary(record({next_eligible_at:"2026-08-27T13:00:00Z"}), now).state, "Cooling down");
  assert.equal(panel.eligibilitySummary(record({active_occurrence:true}), now).state, "Problem active");
  assert.equal(panel.eligibilitySummary(record(), now).state, "Ready");
});

test("eligibility distinguishes a future availability time", () => {
  const now = new Date("2026-08-27T12:00:00Z");
  const result = panel.eligibilitySummary(record({
    currently_active:false,
    definition:{...record().definition,available_from:"2026-08-27T14:00:00Z"},
  }), now);
  assert.equal(result.state, "Waiting to start");
  assert.match(result.detail, /becomes available/);
});

test("delivery summary distinguishes success, partial success, and failure", () => {
  assert.match(panel.deliverySummary([]), /No delivery attempt/);
  assert.match(panel.deliverySummary([{channel:"notify.phone",success:true}]), /succeeded/);
  assert.match(panel.deliverySummary([
    {channel:"persistent_notification",success:true},
    {channel:"notify.phone",success:false,error:"offline"},
  ]), /1 of 2/);
  assert.match(panel.deliverySummary([{channel:"notify.phone",success:false,error:"offline"}]), /failed/);
});

test("per-record history filters unrelated notifications", () => {
  const context = {history:[
    {notification_id:"abc",event:"triggered"},
    {notification_id:"other",event:"triggered"},
    {notification_id:"abc",event:"resolved"},
  ]};
  assert.deepEqual(panel.historyForRecord.call(context,"abc"), [context.history[0],context.history[2]]);
});

test("detail view surfaces ignored reason, delivery error, and record history", () => {
  const current = record({
    last_ignored_reason:"cooldown",
    last_trigger_at:"2026-08-27T11:30:00Z",
    last_trigger:{type:"numeric_state",entity_id:"sensor.freezer",value:-8},
    last_delivery:[{channel:"notify.phone",success:false,error:"device unavailable"}],
  });
  const context = {
    detailId:"abc",
    records:[current],
    history:[{notification_id:"abc",timestamp:"2026-08-27T11:30:00Z",event:"delivery_failed",summary:"Delivery failed",details:{channel:"notify.phone"}}],
    eligibilitySummary:panel.eligibilitySummary,
    deliverySummary:panel.deliverySummary,
    compactDetails:panel.compactDetails,
    historyForRecord:panel.historyForRecord,
    renderDetailHistory:panel.renderDetailHistory,
    preview:panel.preview,
    triggerSummary:panel.triggerSummary,
    conditionSummary:panel.conditionSummary,
    duration:panel.duration,
  };
  const markup = panel.renderDetails.call(context);
  assert.match(markup,/Why the last match was ignored/);
  assert.match(markup,/cooldown/);
  assert.match(markup,/device unavailable/);
  assert.match(markup,/Delivery failed/);
  assert.match(markup,/sensor\.freezer/);
});

test("cards advertise an explicit details action and no longer open the editor directly", () => {
  const context = {triggerSummary:panel.triggerSummary,eligibilitySummary:panel.eligibilitySummary};
  const markup = panel.renderCard.call(context, record());
  assert.match(markup,/data-details="abc"/);
  assert.match(markup,/data-details-card="abc"/);
  assert.doesNotMatch(markup,/data-open=/);
});
