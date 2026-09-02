import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
const registry = new Map();
globalThis.customElements = {
  get: name => registry.get(name),
  define: (name, value) => registry.set(name, value),
  whenDefined: () => new Promise(() => {}),
};

const { patchRecords } = await import(
  "../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-performance.js"
);

test("live record payloads patch one record without requiring a list refresh", () => {
  const records = [
    {id:"a",name:"A",updated_at:"2026-09-02T09:00:00+00:00"},
    {id:"b",name:"B",updated_at:"2026-09-02T10:00:00+00:00"},
  ];
  const result = patchRecords(records, {
    event:"updated",
    notification_id:"a",
    record:{id:"a",name:"A changed",updated_at:"2026-09-02T11:00:00+00:00"},
  });

  assert.equal(result.requiresRefresh, false);
  assert.deepEqual(result.records.map(item => item.id), ["a", "b"]);
  assert.equal(result.records[0].name, "A changed");
  assert.equal(records[0].name, "A");
});

test("delete payload removes the record even though the backend includes its final snapshot", () => {
  const result = patchRecords(
    [{id:"a"},{id:"b"}],
    {event:"deleted",notification_id:"a",record:{id:"a"}},
  );
  assert.equal(result.requiresRefresh, false);
  assert.deepEqual(result.records, [{id:"b"}]);
});

test("reload and malformed payloads fail safe to a full refresh", () => {
  const records = [{id:"a"}];
  assert.equal(patchRecords(records,{event:"reloaded"}).requiresRefresh, true);
  assert.equal(patchRecords(records,{event:"updated"}).requiresRefresh, true);
});
