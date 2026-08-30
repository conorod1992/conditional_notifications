const correlationUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-correlation.js"
  : "/conditional_notifications_panel_correlation.js";

const { ConditionalNotificationsPanel } = await import(correlationUrl);

const WS = "conditional_notifications";
const LOAD_TIMEOUT_MS = 10000;
const panel = ConditionalNotificationsPanel.prototype;
const originalRender = panel.render;
const originalBindHass = panel.bindHass;
const originalConnectedCallback = panel.connectedCallback;
const originalDisconnectedCallback = panel.disconnectedCallback;
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

function errorMessage(error) {
  return error?.message || String(error || "Unknown error");
}

function withTimeout(promise, timeoutMs = LOAD_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("Home Assistant did not respond in time.")), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

panel.attachConnectionReadyListener = function() {
  const connection = this.hass?.connection;
  if (!connection || this._conditionalNotificationsReadyConnection === connection) return;

  if (this._conditionalNotificationsReadyConnection && this._conditionalNotificationsReadyHandler) {
    this._conditionalNotificationsReadyConnection.removeEventListener(
      "ready",
      this._conditionalNotificationsReadyHandler,
    );
  }

  this._conditionalNotificationsReadyHandler ??= () => { void this.handleConnectionReady(); };
  connection.addEventListener("ready", this._conditionalNotificationsReadyHandler);
  this._conditionalNotificationsReadyConnection = connection;
};

panel.detachConnectionReadyListener = function() {
  if (!this._conditionalNotificationsReadyConnection || !this._conditionalNotificationsReadyHandler) return;
  this._conditionalNotificationsReadyConnection.removeEventListener(
    "ready",
    this._conditionalNotificationsReadyHandler,
  );
  this._conditionalNotificationsReadyConnection = undefined;
};

panel.clearLiveSubscription = function(unsubscribe = false) {
  this.subscriptionGeneration = (this.subscriptionGeneration || 0) + 1;
  this.subscriptionPromise = undefined;
  const oldUnsubscribe = this.unsubscribe;
  this.unsubscribe = undefined;
  if (unsubscribe) oldUnsubscribe?.();
};

panel.ensureSubscription = function() {
  if (!this.hass || this.unsubscribe) return Promise.resolve(this.unsubscribe);
  if (this.subscriptionPromise) return this.subscriptionPromise;

  const connection = this.hass.connection;
  const generation = (this.subscriptionGeneration || 0) + 1;
  this.subscriptionGeneration = generation;

  let promise;
  promise = (async () => {
    try {
      const unsubscribe = await connection.subscribeMessage(
        () => { void this.refresh(); },
        {type:`${WS}/subscribe`},
      );
      if (
        this.subscriptionGeneration !== generation
        || this.hass?.connection !== connection
      ) {
        unsubscribe?.();
        return undefined;
      }
      this.unsubscribe = unsubscribe;
      return unsubscribe;
    } catch (error) {
      if (this.subscriptionGeneration === generation) {
        this.showToast(`Live updates unavailable: ${errorMessage(error)}`);
      }
      return undefined;
    } finally {
      if (this.subscriptionPromise === promise) this.subscriptionPromise = undefined;
    }
  })();

  this.subscriptionPromise = promise;
  return promise;
};

panel.load = function(force = false) {
  if (!this.hass) return Promise.resolve();
  if (!force && this.loadPromise) return this.loadPromise;

  const hass = this.hass;
  const connection = hass.connection;
  const generation = (this.loadGeneration || 0) + 1;
  this.loadGeneration = generation;
  this.loading = true;
  this.loadError = "";
  this.render();

  let promise;
  promise = (async () => {
    try {
      const [records, history] = await withTimeout(Promise.all([
        hass.callWS({type:`${WS}/list`}),
        hass.callWS({type:`${WS}/history`}),
      ]));
      if (
        this.loadGeneration !== generation
        || this.hass?.connection !== connection
      ) return;

      this.records = records;
      this.history = history;
      this.loaded = true;
      this.loading = false;
      this.loadError = "";
      this.render();

      // The initial data is enough to show the page. A slow or reconnecting
      // subscription must never keep the panel behind loading placeholders.
      void this.ensureSubscription();
    } catch (error) {
      if (this.loadGeneration !== generation) return;
      this.loaded = false;
      this.loading = false;
      this.loadError = errorMessage(error);
      this.render();
    } finally {
      if (this.loadPromise === promise) this.loadPromise = undefined;
    }
  })();

  this.loadPromise = promise;
  return promise;
};

panel.refresh = function(force = false) {
  if (!this.hass) return Promise.resolve();
  if (!force && this.refreshPromise) return this.refreshPromise;

  const hass = this.hass;
  const connection = hass.connection;
  const generation = (this.refreshGeneration || 0) + 1;
  this.refreshGeneration = generation;
  let promise;
  promise = (async () => {
    try {
      const [records, history] = await withTimeout(Promise.all([
        hass.callWS({type:`${WS}/list`, query:this.search || undefined}),
        hass.callWS({type:`${WS}/history`}),
      ]));
      if (
        this.refreshGeneration !== generation
        || this.hass?.connection !== connection
      ) return;
      this.records = records;
      this.history = history;
      if (!this.editor) this.render();
    } catch (error) {
      if (
        this.refreshGeneration === generation
        && this.hass?.connection === connection
      ) {
        this.showToast(`Couldn't refresh notifications: ${errorMessage(error)}`);
      }
    } finally {
      if (this.refreshPromise === promise) this.refreshPromise = undefined;
    }
  })();

  this.refreshPromise = promise;
  return promise;
};

panel.handleConnectionReady = function() {
  // Home Assistant does not restore this integration's server-side subscription
  // after a dropped websocket. Discard the stale handle and establish a new one.
  this.clearLiveSubscription(false);

  if (this.loaded) {
    return this.refresh(true).then(() => this.ensureSubscription());
  }

  // Supersede an initial request that may have started on the dead connection.
  return this.load(true);
};

panel.applyHass = function(value) {
  const previousConnection = this._hass?.connection;
  this._hass = value;
  this.bindHass();

  if (!value) return;
  if (previousConnection && previousConnection !== value.connection) {
    void this.handleConnectionReady();
  } else if (!this.loaded && !this.loadPromise) {
    void this.load();
  }
};

// Home Assistant's custom-panel host prefers setProperties() when it exists.
// Handling the full property batch ourselves avoids depending on individual
// custom-element setter timing during a direct/F5 panel bootstrap.
panel.setProperties = function(properties) {
  let rerender = false;

  if (Object.prototype.hasOwnProperty.call(properties, "panel")) {
    this._panel = properties.panel;
  }
  if (Object.prototype.hasOwnProperty.call(properties, "route")) {
    this._route = properties.route;
  }
  if (Object.prototype.hasOwnProperty.call(properties, "narrow")) {
    this._narrow = properties.narrow;
    rerender = true;
  }
  if (Object.prototype.hasOwnProperty.call(properties, "hass")) {
    this.applyHass(properties.hass);
  }

  if (rerender) this.render();
};

Object.defineProperty(panel, "hass", {
  configurable: true,
  get() { return this._hass; },
  set(value) { this.applyHass(value); },
});

panel.bindHass = function() {
  originalBindHass.call(this);
  this.attachConnectionReadyListener();
};

panel.upgradeOwnProperty = function(name) {
  if (!Object.prototype.hasOwnProperty.call(this, name)) return;
  const value = this[name];
  delete this[name];
  this[name] = value;
};

panel.connectedCallback = function() {
  // Recover properties assigned while the element was still unupgraded. This
  // is harmless with current Home Assistant, and protects direct navigation
  // against loader/order changes in the custom-panel host.
  for (const property of ["panel", "hass", "narrow", "route"]) {
    this.upgradeOwnProperty(property);
  }

  originalConnectedCallback.call(this);
  this.attachConnectionReadyListener();
  if (this.hass && !this.loaded && !this.loadPromise) void this.load();
};

panel.disconnectedCallback = function() {
  this.detachConnectionReadyListener();
  this.clearLiveSubscription(true);
  this.loadGeneration = (this.loadGeneration || 0) + 1;
  this.refreshGeneration = (this.refreshGeneration || 0) + 1;
  this.loadPromise = undefined;
  this.refreshPromise = undefined;
  this.loaded = false;
  this.loading = true;
  this.loadError = "";
  originalDisconnectedCallback.call(this);
};

panel.render = function() {
  originalRender.call(this);
  if (!this.loadError || this.loading || !this.shadowRoot) return;

  const content = this.shadowRoot.querySelector(".content");
  if (!content) return;
  content.innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><h2>Couldn't load notifications</h2><p>${esc(this.loadError)}</p><button class="primary" id="retry-load">Retry</button></div>`;
  this.shadowRoot.querySelector("#retry-load")?.addEventListener("click", () => {
    void this.load(true);
  });
};

export { ConditionalNotificationsPanel };
