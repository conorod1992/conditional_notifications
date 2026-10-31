import { ConditionalNotificationsPanel as BasePanel } from "./conditional-notifications-panel-editor-ux.js";

const WS = "conditional_notifications";
const LOAD_TIMEOUT_MS = 10000;
const HISTORY_REFRESH_DELAY_MS = 500;

function errorMessage(error) {
  return error?.message || String(error || "Unknown error");
}

function withTimeout(promise, timeoutMs = LOAD_TIMEOUT_MS) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error("Home Assistant did not respond in time.")),
      timeoutMs,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export function patchRecords(records, payload) {
  if (!payload || payload.event === "reloaded") {
    return { records, requiresRefresh: true };
  }
  const id = payload.notification_id || payload.record?.id;
  if (!id) return { records, requiresRefresh: true };
  if (payload.event === "deleted") {
    return {
      records: records.filter((record) => record.id !== id),
      requiresRefresh: false,
    };
  }
  if (!payload.record) return { records, requiresRefresh: true };

  const next = records.filter((record) => record.id !== id);
  next.push(payload.record);
  next.sort((left, right) =>
    String(right.updated_at || "").localeCompare(String(left.updated_at || "")),
  );
  return { records: next, requiresRefresh: false };
}

class ConditionalNotificationsPanel extends BasePanel {
  load(force = false) {
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
        const records = await withTimeout(hass.callWS({ type: `${WS}/list` }));
        if (
          this.loadGeneration !== generation ||
          this.hass?.connection !== connection
        )
          return;

        this.records = records;
        this.loaded = true;
        this.loading = false;
        this.loadError = "";
        this.render();
        void this.ensureSubscription();
        void this.loadPanelMetadata?.();
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
  }

  refresh(force = false) {
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
        const requests = [hass.callWS({ type: `${WS}/list` })];
        if (includeHistory)
          requests.push(hass.callWS({ type: `${WS}/history` }));
        const results = await withTimeout(Promise.all(requests));
        if (
          this.refreshGeneration !== generation ||
          this.hass?.connection !== connection
        )
          return;

        this.records = results[0];
        void this.loadPanelMetadata?.();
        if (includeHistory) {
          this.history = results[1];
          this.historyLoaded = true;
          this.historyLoadError = "";
        }
        if (!this.editor) this.render();
      } catch (error) {
        if (
          this.refreshGeneration === generation &&
          this.hass?.connection === connection
        ) {
          this.showToast(
            `Couldn't refresh notifications: ${errorMessage(error)}`,
          );
        }
      } finally {
        if (this.refreshPromise === promise) this.refreshPromise = undefined;
      }
    })();

    this.refreshPromise = promise;
    return promise;
  }

  ensureHistoryLoaded(force = false) {
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
        const history = await withTimeout(
          hass.callWS({ type: `${WS}/history` }),
        );
        if (
          this.historyGeneration !== generation ||
          this.hass?.connection !== connection
        )
          return this.history;
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
        if (this.historyPromise === promise) {
          this.historyPromise = undefined;
          if (this.historyGeneration === generation)
            this.historyLoading = false;
        }
      }
    })();

    this.historyPromise = promise;
    return promise;
  }

  scheduleHistoryRefresh() {
    if (!this.historyLoaded || !this.hass) return;
    clearTimeout(this.historyRefreshTimer);
    this.historyRefreshTimer = setTimeout(() => {
      this.historyRefreshTimer = undefined;
      void this.ensureHistoryLoaded(true);
    }, HISTORY_REFRESH_DELAY_MS);
  }

  applyLivePayload(payload) {
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
  }

  ensureSubscription() {
    if (!this.hass || this.unsubscribe)
      return Promise.resolve(this.unsubscribe);
    if (this.subscriptionPromise) return this.subscriptionPromise;

    const connection = this.hass.connection;
    const generation = (this.subscriptionGeneration || 0) + 1;
    this.subscriptionGeneration = generation;

    let promise;
    promise = (async () => {
      try {
        const unsubscribe = await connection.subscribeMessage(
          (payload) => this.applyLivePayload(payload),
          { type: `${WS}/subscribe` },
        );
        if (
          this.subscriptionGeneration !== generation ||
          this.hass?.connection !== connection
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
        if (this.subscriptionPromise === promise)
          this.subscriptionPromise = undefined;
      }
    })();

    this.subscriptionPromise = promise;
    return promise;
  }

  bind() {
    super.bind();
    if (!this.shadowRoot) return;
    const root = this.shadowRoot;

    root
      .querySelector('[data-tab="history"]')
      ?.addEventListener("click", () => {
        void this.ensureHistoryLoaded();
      });

    if (!this._conditionalNotificationsHistoryClickHandler) {
      this._conditionalNotificationsHistoryClickHandler = (event) => {
        const target = event.composedPath?.()[0] || event.target;
        if (target?.closest?.("[data-details],[data-details-card]")) {
          void this.ensureHistoryLoaded();
        }
      };
      root.addEventListener(
        "click",
        this._conditionalNotificationsHistoryClickHandler,
        { capture: true },
      );
    }
  }

  render() {
    super.render();
    if (!this.shadowRoot) return;
    const root = this.shadowRoot;
    this.bindModalFocus();
    const timezone = root.querySelector('[data-option-key="schedule"]');
    if (timezone && !timezone.querySelector(".timezone-note")) {
      const note = document.createElement("p");
      note.className = "timezone-note";
      note.textContent = `Dates use your browser timezone (${Intl.DateTimeFormat().resolvedOptions().timeZone}). Recurring hours use Home Assistant (${this.preferences?.time_zone || this.hass?.config?.time_zone || "configured timezone"}).`;
      timezone.append(note);
    }
    if (this.quarantined?.length) {
      const warning = document.createElement("details");
      warning.className = "quarantine-warning";
      const summary = document.createElement("summary");
      summary.textContent = `${this.quarantined.length} notification(s) need repair`;
      warning.append(summary);
      for (const item of this.quarantined) {
        const row = document.createElement("p");
        row.textContent = `${item.name}: ${item.reason}. `;
        const repair = document.createElement("button");
        repair.className = "secondary";
        repair.textContent = "Inspect and repair";
        repair.addEventListener("click", () =>
          this.openEditor({ ...item, quarantined: true }),
        );
        row.append(repair);
        warning.append(row);
      }
      root.querySelector("main")?.prepend(warning);
    }
    if (!this.historyLoaded) {
      root.querySelector('[data-tab="history"] .count')?.remove();
    }
    if (this.tab !== "history" || this.loading) return;

    const content = root.querySelector(".content");
    if (!content) return;
    if (this.historyLoading) {
      content.innerHTML =
        '<div class="cards"><div class="skeleton"></div><div class="skeleton"></div></div>';
      return;
    }
    if (this.historyLoadError && !this.historyLoaded) {
      content.innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><h2>Couldn't load history</h2><p>${String(this.historyLoadError).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c])}</p><button class="primary" id="retry-history">Retry</button></div>`;
      root.querySelector("#retry-history")?.addEventListener("click", () => {
        void this.ensureHistoryLoaded(true);
      });
    }
  }

  bindModalFocus() {
    const root = this.shadowRoot;
    for (const dialog of root.querySelectorAll('[role="dialog"]')) {
      if (dialog.dataset.focusBound) continue;
      dialog.dataset.focusBound = "true";
      const background = root.querySelector("main");
      if (background) background.inert = true;
      dialog.addEventListener("keydown", (event) => {
        if (event.key !== "Tab" || event.defaultPrevented) return;
        const focusable = [
          ...dialog.querySelectorAll(
            'button,a[href],input,select,textarea,ha-selector,ha-entity-picker,[tabindex="0"]',
          ),
        ].filter(
          (element) =>
            !element.disabled &&
            !element.closest("[hidden]") &&
            element.getClientRects().length,
        );
        if (!focusable.length) {
          event.preventDefault();
          return;
        }
        const active = root.activeElement;
        const first = focusable[0],
          last = focusable.at(-1);
        if (event.shiftKey && (active === first || !dialog.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (
          !event.shiftKey &&
          (active === last || !dialog.contains(active))
        ) {
          event.preventDefault();
          first.focus();
        }
      });
    }
  }

  async loadPanelMetadata() {
    const connection = this.hass?.connection;
    if (!connection || this.metadataPromise) return this.metadataPromise;
    this.metadataPromise = Promise.all([
      this.hass.callWS({ type: `${WS}/preferences` }),
      this.hass.callWS({ type: `${WS}/quarantine` }),
    ])
      .then(([preferences, quarantined]) => {
        if (this.hass?.connection !== connection) return;
        this.preferences = preferences;
        this.quarantined = quarantined;
        if (!this.editor) this.render();
      })
      .catch((error) =>
        this.showToast(`Couldn't load repair status: ${errorMessage(error)}`),
      )
      .finally(() => {
        this.metadataPromise = undefined;
      });
    return this.metadataPromise;
  }

  disconnectedCallback() {
    if (this._conditionalNotificationsHistoryClickHandler && this.shadowRoot) {
      this.shadowRoot.removeEventListener(
        "click",
        this._conditionalNotificationsHistoryClickHandler,
        { capture: true },
      );
      this._conditionalNotificationsHistoryClickHandler = undefined;
    }
    clearTimeout(this.historyRefreshTimer);
    this.historyRefreshTimer = undefined;
    this.historyGeneration = (this.historyGeneration || 0) + 1;
    this.historyPromise = undefined;
    this.historyLoaded = false;
    this.historyLoading = false;
    this.historyLoadError = "";
    this.history = [];
    super.disconnectedCallback();
  }
}

export { ConditionalNotificationsPanel };

if (!customElements.get("conditional-notifications-panel")) {
  customElements.define(
    "conditional-notifications-panel",
    ConditionalNotificationsPanel,
  );
}
