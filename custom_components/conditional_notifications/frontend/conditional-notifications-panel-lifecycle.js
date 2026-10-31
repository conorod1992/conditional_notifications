import { ConditionalNotificationsPanel as BasePanel } from "./conditional-notifications-panel-correlation.js";

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>'"]/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        c
      ],
  );

// Home Assistant's custom-panel host prefers setProperties() when it exists.
// Handling the full property batch ourselves avoids depending on individual
// custom-element setter timing during a direct/F5 panel bootstrap.

class ConditionalNotificationsPanel extends BasePanel {
  attachConnectionReadyListener() {
    const connection = this.hass?.connection;
    if (
      !connection ||
      this._conditionalNotificationsReadyConnection === connection
    )
      return;

    if (
      this._conditionalNotificationsReadyConnection &&
      this._conditionalNotificationsReadyHandler
    ) {
      this._conditionalNotificationsReadyConnection.removeEventListener(
        "ready",
        this._conditionalNotificationsReadyHandler,
      );
    }

    this._conditionalNotificationsReadyHandler ??= () => {
      void this.handleConnectionReady();
    };
    connection.addEventListener(
      "ready",
      this._conditionalNotificationsReadyHandler,
    );
    this._conditionalNotificationsReadyConnection = connection;
  }

  detachConnectionReadyListener() {
    if (
      !this._conditionalNotificationsReadyConnection ||
      !this._conditionalNotificationsReadyHandler
    )
      return;
    this._conditionalNotificationsReadyConnection.removeEventListener(
      "ready",
      this._conditionalNotificationsReadyHandler,
    );
    this._conditionalNotificationsReadyConnection = undefined;
  }

  clearLiveSubscription(unsubscribe = false) {
    this.subscriptionGeneration = (this.subscriptionGeneration || 0) + 1;
    this.subscriptionPromise = undefined;
    const oldUnsubscribe = this.unsubscribe;
    this.unsubscribe = undefined;
    if (unsubscribe) oldUnsubscribe?.();
  }

  handleConnectionReady() {
    // Home Assistant does not restore this integration's server-side subscription
    // after a dropped websocket. Discard the stale handle and establish a new one.
    this.clearLiveSubscription(false);

    if (this.loaded) {
      return this.refresh(true).then(() => this.ensureSubscription());
    }

    // Supersede an initial request that may have started on the dead connection.
    return this.load(true);
  }

  applyHass(value) {
    const previousConnection = this._hass?.connection;
    this._hass = value;
    this.bindHass();

    if (!value) return;
    if (previousConnection && previousConnection !== value.connection) {
      void this.handleConnectionReady();
    } else if (!this.loaded && !this.loadPromise) {
      void this.load();
    }
  }

  setProperties(properties) {
    let rerender = false;

    if (Object.prototype.hasOwnProperty.call(properties, "panel")) {
      this._panel = properties.panel;
    }
    if (Object.prototype.hasOwnProperty.call(properties, "route")) {
      this._route = properties.route;
    }
    if (Object.prototype.hasOwnProperty.call(properties, "narrow")) {
      if (this._narrow !== properties.narrow) {
        this._narrow = properties.narrow;
        rerender = true;
      }
    }
    if (Object.prototype.hasOwnProperty.call(properties, "hass")) {
      this.applyHass(properties.hass);
    }

    if (rerender) this.render();
  }

  bindHass() {
    super.bindHass();
    this.attachConnectionReadyListener();
  }

  upgradeOwnProperty(name) {
    if (!Object.prototype.hasOwnProperty.call(this, name)) return;
    const value = this[name];
    delete this[name];
    this[name] = value;
  }

  connectedCallback() {
    // Recover properties assigned while the element was still unupgraded. This
    // is harmless with current Home Assistant, and protects direct navigation
    // against loader/order changes in the custom-panel host.
    for (const property of ["panel", "hass", "narrow", "route"]) {
      this.upgradeOwnProperty(property);
    }

    super.connectedCallback();
    this.attachConnectionReadyListener();
    if (this.hass && !this.loaded && !this.loadPromise) void this.load();
  }

  disconnectedCallback() {
    this.detachConnectionReadyListener();
    this.clearLiveSubscription(true);
    this.loadGeneration = (this.loadGeneration || 0) + 1;
    this.refreshGeneration = (this.refreshGeneration || 0) + 1;
    this.loadPromise = undefined;
    this.refreshPromise = undefined;
    this.loaded = false;
    this.loading = true;
    this.loadError = "";
    super.disconnectedCallback();
  }

  render() {
    super.render();
    if (!this.loadError || this.loading || !this.shadowRoot) return;

    const content = this.shadowRoot.querySelector(".content");
    if (!content) return;
    content.innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><h2>Couldn't load notifications</h2><p>${esc(this.loadError)}</p><button class="primary" id="retry-load">Retry</button></div>`;
    this.shadowRoot
      .querySelector("#retry-load")
      ?.addEventListener("click", () => {
        void this.load(true);
      });
  }

  get hass() {
    return this._hass;
  }
  set hass(value) {
    this.applyHass(value);
  }
}

export { ConditionalNotificationsPanel };
