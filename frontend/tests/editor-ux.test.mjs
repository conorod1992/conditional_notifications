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
  activeAdvancedOptions,
  editorErrorItems,
} = await import("../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-editor-ux.js");

test("advanced option summary only lists configured groups", () => {
  assert.deepEqual(
    activeAdvancedOptions({
      expires_at:"2026-09-03T12:00:00+01:00",
      cooldown:300,
      active_window:{start:"22:00",end:"07:00"},
      resolve_when:{trigger:"state",entity_id:"binary_sensor.door",to:"off"},
      match:"all_within",
      delivery:{companion:{actions:[{title:"Open",uri:"/lovelace"}]}},
    }),
    [
      {key:"schedule",label:"Schedule"},
      {key:"timing",label:"Timing"},
      {key:"recurring",label:"Hours"},
      {key:"resolution",label:"Resolution"},
      {key:"correlation",label:"Correlation"},
      {key:"companion",label:"Companion App"},
    ],
  );

  assert.deepEqual(activeAdvancedOptions({delivery:{use_defaults:true},match:"any"}), []);
});

test("validation errors map to the section a user needs to fix", () => {
  assert.deepEqual(
    editorErrorItems({
      name:"Give this notification a name.",
      trigger0:"Choose an entity.",
      condition1:"Choose an entity.",
      delivery:"Choose at least one delivery channel.",
      repeat:"Enter a positive count.",
      expires_at:"Expiry must be after availability.",
      companion:"Give each Companion App button a title.",
    }),
    [
      {key:"name",message:"Give this notification a name.",section:"Basics"},
      {key:"trigger0",message:"Choose an entity.",section:"When"},
      {key:"condition1",message:"Choose an entity.",section:"Only if"},
      {key:"delivery",message:"Choose at least one delivery channel.",section:"Send"},
      {key:"repeat",message:"Enter a positive count.",section:"After notifying"},
      {key:"expires_at",message:"Expiry must be after availability.",section:"More options",optionKey:"schedule"},
      {key:"companion",message:"Give each Companion App button a title.",section:"More options",optionKey:"companion"},
    ],
  );
});
