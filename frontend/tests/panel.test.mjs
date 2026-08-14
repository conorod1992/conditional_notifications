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

test("typing updates editor state without replacing the focused form", () => {
  let renders = 0;
  let previews = 0;
  const context = {
    editor:{definition:{name:""}},
    markDirty() {},
    render() { renders += 1; },
    updatePreview() { previews += 1; },
  };
  panel.onField.call(context, {
    type:"input",
    currentTarget:{dataset:{path:"name"}, type:"text", value:"Kitchen"},
  });
  assert.equal(context.editor.definition.name, "Kitchen");
  assert.equal(renders, 0);
  assert.equal(previews, 1);
});

test("only structural field changes rebuild the editor", () => {
  let renders = 0;
  const context = {
    editor:{definition:{repeat_policy:"once"}},
    markDirty() {},
    render() { renders += 1; },
    updatePreview() {},
  };
  panel.onField.call(context, {
    type:"change",
    currentTarget:{dataset:{path:"repeat_policy"}, type:"radio", value:"limited"},
  });
  assert.equal(context.editor.definition.repeat_policy, "limited");
  assert.equal(renders, 1);
});

test("live record refresh does not replace an open editor", async () => {
  let renders = 0;
  const context = {
    editor:{definition:{}}, search:"", records:[], history:[],
    hass:{callWS:async ({type}) => type.endsWith("/list") ? ["record"] : ["history"]},
    render() { renders += 1; },
  };
  await panel.refresh.call(context);
  assert.deepEqual(context.records, ["record"]);
  assert.deepEqual(context.history, ["history"]);
  assert.equal(renders, 0);
});

test("Home Assistant updates do not reset an initialized entity picker", () => {
  const picker = {dataset:{value:"light.kitchen", domain:"light"}};
  const context = {
    hass:{states:{}},
    shadowRoot:{querySelectorAll:() => [picker]},
  };
  panel.bindHass.call(context);
  assert.equal(picker.value, "light.kitchen");
  assert.deepEqual(picker.includeDomains, ["light"]);
  picker.value = "light.office";
  panel.bindHass.call(context);
  assert.equal(picker.value, "light.office");
});

test("advanced section and editor scroll survive structural renders", () => {
  const oldBody = {scrollTop:742};
  const context = {
    advancedOpen:true,
    editorScrollTop:0,
    shadowRoot:{querySelector:selector => selector === ".editor-body" ? oldBody : null},
  };
  panel.captureEditorState.call(context);
  assert.equal(context.editorScrollTop, 742);

  const newBody = {scrollTop:0};
  const details = {open:false};
  context.shadowRoot.querySelector = selector => selector === ".editor-body" ? newBody : details;
  panel.restoreEditorState.call(context);
  assert.equal(newBody.scrollTop, 742);
  assert.equal(details.open, true);
});

test("datetime values are displayed in browser-local time", () => {
  const value = "2026-08-15T10:30:00Z";
  const date = new Date(value);
  const pad = number => String(number).padStart(2, "0");
  const expected = `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  assert.equal(panel.dateTimeValue(value), expected);
});

test("custom delivery requires a selected channel", () => {
  const context = {};
  const baseDefinition = {
    name:"Test", triggers:[{type:"state",entity_id:"binary_sensor.door"}],
    title:"Door", message:"Opened", repeat_policy:"once",
    delivery:{use_defaults:false,persistent_notification:false,notify_entities:[]},
  };
  assert.match(panel.validate.call(context, baseDefinition).delivery, /at least one/);
  const valid = structuredClone(baseDefinition);
  valid.delivery.notify_entities = ["notify.phone"];
  assert.equal(panel.validate.call(context, valid).delivery, undefined);
});
