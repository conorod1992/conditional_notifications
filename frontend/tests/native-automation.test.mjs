import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
const registry = new Map();
globalThis.customElements = {
  get: name => registry.get(name),
  define: (name, value) => registry.set(name, value),
  whenDefined: () => new Promise(() => {}),
};

const {
  constrainNativeAutomationTree,
  mergeNativeTriggers,
  simpleCurrentStateCandidate,
  syncNativeAutomationSelectorValue,
  toNativeCondition,
  toNativeTrigger,
} = await import("../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-native-automation.js");

test("legacy triggers convert to native HA syntax without changing named triggers", () => {
  assert.deepEqual(
    toNativeTrigger({
      type:"state",
      entity_id:"binary_sensor.door",
      from:"off",
      to:"on",
      for:30,
    }),
    {
      trigger:"state",
      entity_id:"binary_sensor.door",
      from:"off",
      to:"on",
      for:30,
    },
  );
  assert.deepEqual(
    toNativeTrigger({
      type:"zone",
      entity_id:"person.conor",
      zone_entity_id:"zone.home",
      event:"enter",
    }),
    {
      trigger:"zone",
      entity_id:"person.conor",
      zone:"zone.home",
      event:"enter",
    },
  );
  assert.equal(toNativeTrigger({type:"named",trigger_id:"external"}), null);
});

test("legacy conditions convert to HA conditions including negation and weekdays", () => {
  assert.deepEqual(
    toNativeCondition({
      type:"state",
      entity_id:"person.conor",
      state:"home",
      negate:true,
    }),
    {
      condition:"not",
      conditions:[{condition:"state",entity_id:"person.conor",state:"home"}],
    },
  );
  assert.deepEqual(
    toNativeCondition({
      type:"time",
      after:"09:00",
      before:"17:00",
      weekdays:["monday","friday"],
    }),
    {
      condition:"time",
      after:"09:00",
      before:"17:00",
      weekday:["mon","fri"],
    },
  );
});

test("native editor changes preserve external trigger slots", () => {
  const existing = [
    {type:"named",trigger_id:"before"},
    {type:"state",entity_id:"binary_sensor.old",to:"on"},
    {type:"named",trigger_id:"after"},
  ];
  const replacement = [
    {trigger:"time",at:"08:00:00"},
    {trigger:"sun",event:"sunset"},
  ];
  assert.deepEqual(mergeNativeTriggers(existing,replacement),[
    {type:"named",trigger_id:"before"},
    {trigger:"time",at:"08:00:00"},
    {type:"named",trigger_id:"after"},
    {trigger:"sun",event:"sunset"},
  ]);
});

test("current-state option is limited to simple state triggers", () => {
  assert.equal(simpleCurrentStateCandidate({type:"state",entity_id:"binary_sensor.door",to:"on"}),true);
  assert.equal(simpleCurrentStateCandidate({trigger:"state",entity_id:"binary_sensor.door",to:["on","off"]}),false);
  assert.equal(simpleCurrentStateCandidate({trigger:"time",at:"08:00:00"}),false);
});

test("already-native trigger groups are preserved for backend OR semantics", () => {
  const group = {
    triggers:[
      {trigger:"state",entity_id:"binary_sensor.a",to:"on"},
      {trigger:"state",entity_id:"binary_sensor.b",to:"on"},
    ],
  };
  assert.deepEqual(toNativeTrigger(group),group);
  assert.notEqual(toNativeTrigger(group),group);
});


test("native HA selectors receive emitted values back immediately", () => {
  const selector = {value:undefined};
  const emitted = [{trigger:"time",at:"08:00:00"}];

  syncNativeAutomationSelectorValue(selector, emitted);

  assert.deepEqual(selector.value, emitted);
  assert.notEqual(selector.value, emitted);
  emitted[0].at = "09:00:00";
  assert.equal(selector.value[0].at, "08:00:00");
});


test("embedded native automation hosts are constrained to their container", () => {
  const card = {localName:"ha-card", style:{}, shadowRoot:null};
  const ignored = {localName:"span", style:{}, shadowRoot:null};
  const nestedRoot = {querySelectorAll:() => [card, ignored]};
  const row = {localName:"ha-automation-trigger-row", style:{}, shadowRoot:nestedRoot};
  const root = {querySelectorAll:() => [row]};

  constrainNativeAutomationTree(root);

  assert.equal(row.style.minWidth, "0");
  assert.equal(row.style.maxWidth, "100%");
  assert.equal(row.style.width, "100%");
  assert.equal(row.style.boxSizing, "border-box");
  assert.equal(card.style.maxWidth, "100%");
  assert.deepEqual(ignored.style, {});
});
