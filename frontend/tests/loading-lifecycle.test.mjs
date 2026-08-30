import assert from "node:assert/strict";
import test from "node:test";

globalThis.HTMLElement = class {};
globalThis.customElements = {get: () => undefined, define: () => {}};

const { ConditionalNotificationsPanel } = await import(
  "../../custom_components/conditional_notifications/frontend/conditional-notifications-panel-lifecycle.js"
);
const panel = ConditionalNotificationsPanel.prototype;

function contextWith(overrides = {}) {
  const {hass, ...rest} = overrides;
  const context = Object.assign(Object.create(panel), {
    records: [],
    history: [],
    search: "",
    loading: true,
    loaded: false,
    render() {},
    showToast(message) { this.toastMessage = message; },
  }, rest);
  if (hass !== undefined) {
    // Tests exercise lifecycle methods directly; bypass the real custom-element
    // setter, which intentionally starts loading as soon as Home Assistant sets it.
    Object.defineProperty(context, "hass", {
      value: hass,
      writable: true,
      configurable: true,
    });
  }
  return context;
}

test("initial data load does not wait for the live subscription", async () => {
  let resolveSubscription;
  const connection = {
    subscribeMessage: () => new Promise((resolve) => { resolveSubscription = resolve; }),
  };
  const context = contextWith({
    hass: {
      connection,
      callWS: async ({type}) => type.endsWith("/list") ? [{id:"one"}] : [{event:"created"}],
    },
  });

  await context.load();

  assert.equal(context.loaded, true);
  assert.equal(context.loading, false);
  assert.deepEqual(context.records, [{id:"one"}]);
  assert.deepEqual(context.history, [{event:"created"}]);
  assert.ok(context.subscriptionPromise, "subscription should continue independently");

  resolveSubscription(() => {});
  await context.subscriptionPromise;
});

test("failed initial data load becomes retryable instead of staying on skeletons", async () => {
  const connection = {subscribeMessage: async () => () => {}};
  const context = contextWith({
    hass: {
      connection,
      callWS: async () => { throw new Error("socket unavailable"); },
    },
  });

  await context.load();

  assert.equal(context.loaded, false);
  assert.equal(context.loading, false);
  assert.match(context.loadError, /socket unavailable/);
});

test("connection ready supersedes a stale initial load", async () => {
  const staleLoad = new Promise(() => {});
  const connection = {subscribeMessage: async () => () => {}};
  const context = contextWith({
    loadPromise: staleLoad,
    loadGeneration: 1,
    hass: {
      connection,
      callWS: async ({type}) => type.endsWith("/list") ? [{id:"fresh"}] : [{event:"fresh"}],
    },
  });

  await context.handleConnectionReady();

  assert.equal(context.loaded, true);
  assert.deepEqual(context.records, [{id:"fresh"}]);
  assert.deepEqual(context.history, [{event:"fresh"}]);
});

test("connection ready supersedes a stale refresh and restores live updates", async () => {
  const staleRefresh = new Promise(() => {});
  let subscriptions = 0;
  const connection = {
    subscribeMessage: async () => {
      subscriptions += 1;
      return () => {};
    },
  };
  const context = contextWith({
    loaded: true,
    refreshPromise: staleRefresh,
    refreshGeneration: 3,
    records: [{id:"old"}],
    hass: {
      connection,
      callWS: async ({type}) => type.endsWith("/list") ? [{id:"fresh"}] : [{event:"fresh"}],
    },
  });

  await context.handleConnectionReady();

  assert.deepEqual(context.records, [{id:"fresh"}]);
  assert.equal(subscriptions, 1);
  assert.equal(typeof context.unsubscribe, "function");
});

test("refresh failures preserve the last good data and are handled", async () => {
  const previousRecords = [{id:"existing"}];
  const previousHistory = [{event:"existing"}];
  const connection = {};
  const context = contextWith({
    records: previousRecords,
    history: previousHistory,
    loaded: true,
    hass: {
      connection,
      callWS: async () => { throw new Error("temporary disconnect"); },
    },
  });

  await context.refresh();

  assert.equal(context.records, previousRecords);
  assert.equal(context.history, previousHistory);
  assert.match(context.toastMessage, /temporary disconnect/);
});

test("ready listener follows the active Home Assistant connection", () => {
  const calls = [];
  const first = {
    addEventListener: (name, handler) => calls.push(["add-first", name, handler]),
    removeEventListener: (name, handler) => calls.push(["remove-first", name, handler]),
  };
  const second = {
    addEventListener: (name, handler) => calls.push(["add-second", name, handler]),
    removeEventListener: (name, handler) => calls.push(["remove-second", name, handler]),
  };
  const context = contextWith({hass:{connection:first}});

  context.attachConnectionReadyListener();
  context.attachConnectionReadyListener();
  context.hass = {connection:second};
  context.attachConnectionReadyListener();
  context.detachConnectionReadyListener();

  assert.deepEqual(calls.map(([action, name]) => [action, name]), [
    ["add-first", "ready"],
    ["remove-first", "ready"],
    ["add-second", "ready"],
    ["remove-second", "ready"],
  ]);
});
