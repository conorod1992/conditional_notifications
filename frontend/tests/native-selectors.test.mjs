import assert from "node:assert/strict";
import test from "node:test";

const registry = new Map();
globalThis.HTMLElement = class {};
globalThis.customElements = {
  get: name => registry.get(name),
  define: (name, value) => registry.set(name, value),
  whenDefined: () => new Promise(() => {}),
};

const {
  ConditionalNotificationsPanel,
  durationValueToSeconds,
} = await import("../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-entry.js");
const panel = ConditionalNotificationsPanel.prototype;

test("native duration values are converted back to the existing seconds schema", () => {
  assert.equal(durationValueToSeconds(90), 90);
  assert.equal(durationValueToSeconds("01:02:03"), 3723);
  assert.equal(
    durationValueToSeconds({days:1, hours:2, minutes:3, seconds:4}),
    93784,
  );
  assert.equal(durationValueToSeconds({minutes:2, seconds:30}), 150);
  assert.equal(durationValueToSeconds(undefined), undefined);
  assert.equal(durationValueToSeconds({minutes:"bad"}), undefined);
});

test("native state selector updates preserve the existing definition shape", () => {
  let dirty = 0;
  let previews = 0;
  const context = {
    editor:{definition:{triggers:[{type:"state", entity_id:"binary_sensor.door", from:"off", to:"on"}]}},
    markDirty(){dirty += 1;},
    updatePreview(){previews += 1;},
  };

  assert.equal(panel.applyNativeSelectorValue.call(context, "triggers.0.to", "off"), true);
  assert.equal(context.editor.definition.triggers[0].to, "off");
  assert.equal(panel.applyNativeSelectorValue.call(context, "triggers.0.from", ""), true);
  assert.equal("from" in context.editor.definition.triggers[0], false);
  assert.equal(dirty, 2);
  assert.equal(previews, 2);
});

test("native duration selector stores scalar seconds, not HA duration objects", () => {
  const context = {
    editor:{definition:{cooldown:60}},
    markDirty(){this.dirty = true;},
    updatePreview(){this.previewed = true;},
  };

  assert.equal(
    panel.applyNativeSelectorValue.call(
      context,
      "cooldown",
      {hours:1, minutes:15, seconds:5},
      "duration",
    ),
    true,
  );
  assert.equal(context.editor.definition.cooldown, 4505);
  assert.equal(typeof context.editor.definition.cooldown, "number");
  assert.equal(context.dirty, true);
  assert.equal(context.previewed, true);
});

test("invalid duration selector payloads preserve the previous value", () => {
  let dirty = 0;
  const context = {
    editor:{definition:{cooldown:120}},
    markDirty(){dirty += 1;},
    updatePreview(){throw new Error("preview should not update");},
  };

  assert.equal(
    panel.applyNativeSelectorValue.call(
      context,
      "cooldown",
      {minutes:"invalid"},
      "duration",
    ),
    false,
  );
  assert.equal(context.editor.definition.cooldown, 120);
  assert.equal(dirty, 0);
});

test("cleared or zero native durations retain the existing optional-duration semantics", () => {
  const context = {
    editor:{definition:{cooldown:120, debounce:30}},
    markDirty(){},
    updatePreview(){},
  };

  assert.equal(panel.applyNativeSelectorValue.call(context, "cooldown", undefined, "duration"), true);
  assert.equal("cooldown" in context.editor.definition, false);
  assert.equal(
    panel.applyNativeSelectorValue.call(context, "debounce", {seconds:0}, "duration"),
    true,
  );
  assert.equal("debounce" in context.editor.definition, false);
});

test("selector-backed edits keep optimistic revision WebSocket compatibility", async () => {
  const calls = [];
  const definition = {
    name:"Door",
    triggers:[{type:"state", entity_id:"binary_sensor.door", to:"on"}],
    conditions:[],
    title:"Door",
    message:"Opened",
    repeat_policy:"once",
    delivery:{use_defaults:true},
  };
  const context = {
    editor:{id:"record-1", original:{revision:7}, definition},
    validate:()=>({}),
    hass:{callWS:async message => calls.push(message)},
    closeEditor(){this.editor = null;},
    showToast(){},
    async refresh(){},
  };

  await panel.save.call(context);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, "conditional_notifications/update");
  assert.equal(calls[0].expected_revision, 7);
  assert.deepEqual(calls[0].changes, definition);
});

test("duplicate save clicks share one WebSocket mutation", async () => {
  let resolveSave;
  let calls = 0;
  const pending = new Promise(resolve => { resolveSave = resolve; });
  const context = {
    editor:{id:undefined, definition:{name:"New"}},
    validate:()=>({}),
    hass:{callWS:async () => { calls += 1; await pending; }},
    closeEditor(){this.editor = null;},
    showToast(){},
    async refresh(){},
  };

  const first = panel.save.call(context);
  const second = panel.save.call(context);
  assert.equal(first, second);
  assert.equal(calls, 1);
  resolveSave();
  await first;
  assert.equal(calls, 1);
});
