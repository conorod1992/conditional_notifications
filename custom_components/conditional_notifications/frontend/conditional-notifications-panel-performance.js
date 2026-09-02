const editorUxUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-editor-ux.js"
  : "/conditional_notifications_panel_editor_ux.js";

const { ConditionalNotificationsPanel } = await import(editorUxUrl);

const WS = "conditional_notifications";
const LOAD_TIMEOUT_MS = 10000;
const HISTORY_REFRESH_DELAY_MS = 500;
const panel = ConditionalNotificationsPanel.prototype;
const originalBind = panel.bind;
const originalRender = panel.render;
const originalDisconnectedCallback = panel.disconnectedCallback;

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

export function patchRecords(records, payload) {
  if (!payload || payload.event === "reloaded") {
    return {records, requiresRefresh:true};
  }
  const id = payload.notification_id || payload.record?.id;
  if (!id) return {records, requiresRefresh:true};
  if (payload.event === "deleted") {
    return {records:records.filter(record => record.id !== id), requiresRefresh:false};
  }
  if (!payload.record) return {records, requiresRefresh:true};

  const next = records.filter(record => record.id !== id);
  next.push(payload.record);
  next.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
  return {records:next, requiresRefresh:false};
}

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
      const records = await withTimeout(hass.callWS({type:`${WS}/list`}));
      if (
        this.loadGeneration !== generation
        || this.hass?.connection !== connection
      ) return;

      this.records = records;
      this.loaded = true;
      this.loading = false;
      this.loadError = "";
      this.render();
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
  const includeHistory = Boolean(this.historyLoaded);

  let promise;
  promise = (async () => {
    try {
      const requests = [hass.callWS({type:`${WS}/list`})];
      if (includeHistory) requests.push(hass.callWS({type:`${WS}/history`}));
      const results = await withTimeout(Promise.all(requests));
      if (
        this.refreshGeneration !== generation
        || this.hass?.connection !== connection
      ) return;

      this.records = results[0];
      if (includeHistory) {
        this.history = results[1];
        this.historyLoaded = true;
        this.historyLoadError = "";
      }
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

panel.ensureHistoryLoaded = function(force = false) {
  if (!this.hass) return Promise.resolve();
  if (!force && this.historyLoaded) return Promise.resolve(this.history);
  if (this.historyPromise) return this.historyPromise;

  const hass = this.hass;
  const connection = hass.connection;
  const generation = (this.historyGeneration || 0) + 1;
  this.historyGeneration = generation;
  this.historyLoading = true;
  this.historyLoadError = "";
  if (this.tab === "history" && !this.editor) this.render();

  let promise;
  promise = (async () => {
    try {
      const history = await withTimeout(hass.callWS({type:`${WS}/history`}));
      if (
        this.historyGeneration !== generation
        || this.hass?.connection !== connection
      ) return this.history;
      this.history = history;
      this.historyLoaded = true;
      this.historyLoading = false;
      this.historyLoadError = "";
      if (!this.editor) this.render();
      return history;
    } catch (error) {
      if (this.historyGeneration === generation) {
        this.historyLoading = false;
        this.historyLoadError = errorMessage(error);
        if (!this.editor) this.render();
        this.showToast(`Couldn't load history: ${this.historyLoadError}`);
      }
      return this.history;
    } finally {
      if (this.historyPromise === promise) this.historyPromise = undefined;
    }
  })();

  this.historyPromise = promise;
  return promise;
};

panel.scheduleHistoryRefresh = function() {
  if (!this.historyLoaded || !this.hass) return;
  clearTimeout(this.historyRefreshTimer);
  this.historyRefreshTimer = setTimeout(() => {
    this.historyRefreshTimer = undefined;
    void this.ensureHistoryLoaded(true);
  }, HISTORY_REFRESH_DELAY_MS);
};

panel.applyLivePayload = function(payload) {
  if (payload?.event === "reloaded") {
    void this.refresh(true);
    return;
  }

  const patched = patchRecords(this.records || [], payload);
  if (patched.requiresRefresh) {
    void this.refresh(true);
    return;
  }

  this.records = patched.records;
  this.scheduleHistoryRefresh();
  if (!this.editor) this.render();
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
        payload => this.applyLivePayload(payload),
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

panel.bind = function() {
  originalBind.call(this);
  if (!this.shadowRoot) return;
  const root = this.shadowRoot;

  root.querySelector('[data-tab="history"]')?.addEventListener("click", () => {
    void this.ensureHistoryLoaded();
  });

  root.addEventListener("click", event => {
    const target = event.composedPath?.()[0] || event.target;
    if (target?.closest?.("[data-details],[data-details-card]")) {
      void this.ensureHistoryLoaded();
    }
  }, {capture:true});
};

panel.render = function() {
  originalRender.call(this);
  if (!this.shadowRoot) return;
  const root = this.shadowRoot;
  if (!this.historyLoaded) {
    root.querySelector('[data-tab="history"] .count')?.remove();
  }
  if (this.tab !== "history" || this.loading) return;

  const content = root.querySelector(".content");
  if (!content) return;
  if (this.historyLoading) {
    content.innerHTML = '<div class="cards"><div class="skeleton"></div><div class="skeleton"></div></div>';
    return;
  }
  if (this.historyLoadError && !this.historyLoaded) {
    content.innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><h2>Couldn't load history</h2><p>${String(this.historyLoadError).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}</p><button class="primary" id="retry-history">Retry</button></div>`;
    root.querySelector("#retry-history")?.addEventListener("click", () => {
      void this.ensureHistoryLoaded(true);
    });
  }
};

panel.disconnectedCallback = function() {
  clearTimeout(this.historyRefreshTimer);
  this.historyRefreshTimer = undefined;
  this.historyGeneration = (this.historyGeneration || 0) + 1;
  this.historyPromise = undefined;
  this.historyLoaded = false;
  this.historyLoading = false;
  this.historyLoadError = "";
  this.history = [];
  originalDisconnectedCallback.call(this);
};

export { ConditionalNotificationsPanel };
