import { ConditionalNotificationsPanel } from "./conditional-notifications-panel-lifecycle.js";

const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;
const originalBind = panel.bind;
const originalStyles = panel.styles;
let nativeSelectorLoadPromise;

function getDefinitionValue(definition, path) {
  return path.split(".").reduce((value, part) => value?.[part], definition);
}

function setDefinitionValue(definition, path, value) {
  const parts = path.split(".");
  let target = definition;
  for (const part of parts.slice(0, -1)) target = target[part];
  const field = parts.at(-1);
  if (value === undefined || value === null || value === "") delete target[field];
  else target[field] = value;
}

function valuesEqual(left, right) {
  if (left === right) return true;
  if (left === undefined && (right === undefined || right === null || right === "")) return true;
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

export function durationValueToSeconds(value) {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "string") {
    const parts = value.split(":").map(Number);
    if (parts.some(part => !Number.isFinite(part))) return undefined;
    if (parts.length === 1) return parts[0];
    if (parts.length === 2) return parts[0] * 3600 + parts[1] * 60;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return undefined;
  }
  if (typeof value !== "object") return undefined;
  const days = Number(value.days || 0);
  const hours = Number(value.hours || 0);
  const minutes = Number(value.minutes || 0);
  const seconds = Number(value.seconds || 0);
  const milliseconds = Number(value.milliseconds || 0);
  const parts = [days, hours, minutes, seconds, milliseconds];
  if (parts.some(part => !Number.isFinite(part))) return undefined;
  return days * 86400 + hours * 3600 + minutes * 60 + seconds + milliseconds / 1000;
}

function nativeSelectorsAvailable() {
  return typeof document !== "undefined"
    && typeof customElements !== "undefined"
    && Boolean(customElements.get?.("ha-selector"));
}

async function waitForCustomElement(name, timeoutMs = 5000) {
  if (typeof customElements === "undefined") throw new Error("Custom elements are unavailable");
  if (customElements.get(name)) return;
  if (typeof customElements.whenDefined !== "function") throw new Error("Custom element loading is unavailable");
  let timer;
  try {
    await Promise.race([
      customElements.whenDefined(name),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`Timed out loading ${name}`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function ensureNativeSelectorsLoaded() {
  if (nativeSelectorsAvailable()) return Promise.resolve(true);
  if (nativeSelectorLoadPromise) return nativeSelectorLoadPromise;
  nativeSelectorLoadPromise = (async () => {
    if (typeof document === "undefined" || typeof customElements === "undefined") return false;
    try {
      await waitForCustomElement("partial-panel-resolver");
      if (!customElements.get("ha-selector")) {
        const resolver = document.createElement("partial-panel-resolver");
        resolver.hass = {
          panels: [{url_path:"conditional-notifications-selector-loader", component_name:"config"}],
        };
        if (typeof resolver._updateRoutes !== "function") return false;
        resolver._updateRoutes();
        const configRoute = resolver.routerOptions?.routes?.["conditional-notifications-selector-loader"];
        if (typeof configRoute?.load !== "function") return false;
        await configRoute.load();
      }
      if (!customElements.get("ha-selector")) {
        await waitForCustomElement("ha-panel-config");
        const configPanel = document.createElement("ha-panel-config");
        const automationRoute = configPanel.routerOptions?.routes?.automation;
        if (typeof automationRoute?.load !== "function") return false;
        await automationRoute.load();
      }
      await waitForCustomElement("ha-selector");
      return nativeSelectorsAvailable();
    } catch (error) {
      console.warn("Conditional Notifications: native HA selectors could not be loaded; using fallback fields", error);
      return false;
    }
  })();
  return nativeSelectorLoadPromise;
}

function selectorForPath(root, path) {
  return root.querySelector(`[data-path="${path}"], [data-native-path="${path}"], [data-native-duration-path="${path}"]`);
}

function markContextSource(root, path) {
  selectorForPath(root, path)?.setAttribute?.("data-native-context-source", "true");
}

function relabel(control, text) {
  const label = control?.closest?.("label");
  if (!label?.childNodes) return;
  const textNode = [...label.childNodes].find(node => node.nodeType === 3 && node.textContent?.trim());
  if (textNode) textNode.textContent = text;
}

function ensureInsertedInput(root, definition, {path, label, anchor, type = "text", min}) {
  if (selectorForPath(root, path) || !anchor || typeof document === "undefined") return;
  const wrapper = document.createElement("label");
  wrapper.className = "native-inserted-field";
  wrapper.append(document.createTextNode(label));
  const input = document.createElement("input");
  input.dataset.path = path;
  input.type = type;
  if (min !== undefined) input.min = String(min);
  const value = getDefinitionValue(definition, path);
  input.value = value ?? "";
  wrapper.append(input);
  anchor.insertAdjacentElement("afterend", wrapper);
}

function replaceWithNativeSelector(instance, path, selector, options = {}) {
  const root = instance.shadowRoot;
  const control = selectorForPath(root, path);
  if (!control || control.localName === "ha-selector" || !nativeSelectorsAvailable()) return control;
  const replacement = document.createElement("ha-selector");
  replacement.className = "native-ha-selector";
  replacement.hass = instance.hass;
  replacement.narrow = Boolean(instance._narrow);
  replacement.selector = selector;
  replacement.value = getDefinitionValue(instance.editor.definition, path);
  replacement.required = Boolean(options.required);
  if (options.entityPath || options.attributePath) {
    replacement.context = {
      filter_entity: options.entityPath
        ? getDefinitionValue(instance.editor.definition, options.entityPath)
        : undefined,
      filter_attribute: options.attributePath
        ? getDefinitionValue(instance.editor.definition, options.attributePath)
        : undefined,
    };
    if (options.entityPath) replacement.dataset.nativeEntityPath = options.entityPath;
    if (options.attributePath) replacement.dataset.nativeAttributePath = options.attributePath;
  }
  if (options.attributeEntityPath) {
    replacement.context = {
      filter_entity: getDefinitionValue(instance.editor.definition, options.attributeEntityPath),
    };
    replacement.dataset.nativeAttributeEntityPath = options.attributeEntityPath;
  }
  replacement.dataset.nativePath = path;
  control.replaceWith(replacement);
  if (options.entityPath) markContextSource(root, options.entityPath);
  if (options.attributePath) markContextSource(root, options.attributePath);
  if (options.attributeEntityPath) markContextSource(root, options.attributeEntityPath);
  return replacement;
}

function replaceWithDurationSelector(instance, path, label) {
  const control = selectorForPath(instance.shadowRoot, path);
  if (!control || control.localName === "ha-selector" || !nativeSelectorsAvailable()) return false;
  const replacement = document.createElement("ha-selector");
  replacement.className = "native-ha-selector";
  replacement.hass = instance.hass;
  replacement.narrow = Boolean(instance._narrow);
  replacement.selector = {duration:{enable_day:true, enable_second:true}};
  replacement.value = getDefinitionValue(instance.editor.definition, path);
  replacement.required = false;
  replacement.dataset.nativeDurationPath = path;
  control.replaceWith(replacement);
  relabel(replacement, label);
  return true;
}

panel.styles = function() {
  return originalStyles.call(this).replace("</style>", `
    ha-selector.native-ha-selector{display:block;width:100%;min-width:0}
    .native-inserted-field{min-width:0}
  </style>`);
};

panel.applyNativeSelectorValue = function(path, value, kind = "value") {
  if (!this.editor) return false;
  let normalized = value;
  if (kind === "duration") {
    const cleared = value === undefined || value === null || value === "";
    normalized = durationValueToSeconds(value);
    if (!cleared && (normalized === undefined || normalized < 0)) return false;
    if (normalized === 0) normalized = undefined;
  }
  if (valuesEqual(getDefinitionValue(this.editor.definition, path), normalized)) return false;
  setDefinitionValue(this.editor.definition, path, normalized);
  this.markDirty();
  this.updatePreview();
  return true;
};

panel.syncNativeSelectorContexts = function() {
  if (!this.editor || !this.shadowRoot) return;
  const definition = this.editor.definition;
  this.shadowRoot.querySelectorAll("ha-selector[data-native-entity-path]").forEach(selector => {
    selector.context = {
      filter_entity: getDefinitionValue(definition, selector.dataset.nativeEntityPath),
      filter_attribute: selector.dataset.nativeAttributePath
        ? getDefinitionValue(definition, selector.dataset.nativeAttributePath)
        : undefined,
    };
  });
  this.shadowRoot.querySelectorAll("ha-selector[data-native-attribute-entity-path]").forEach(selector => {
    selector.context = {
      filter_entity: getDefinitionValue(definition, selector.dataset.nativeAttributeEntityPath),
    };
  });
};

panel.hydrateNativeSelectors = function() {
  if (!this.editor || !this.shadowRoot) return;
  const definition = this.editor.definition;
  const root = this.shadowRoot;

  for (const [index, trigger] of definition.triggers.entries()) {
    if (!["state", "numeric_state"].includes(trigger.type)) continue;
    const base = `triggers.${index}`;
    const entityPath = `${base}.entity_id`;
    const entityControl = selectorForPath(root, entityPath);
    ensureInsertedInput(root, definition, {
      path: `${base}.attribute`,
      label: "Attribute (optional)",
      anchor: entityControl?.closest?.("label"),
    });
    replaceWithNativeSelector(this, `${base}.attribute`, {attribute:{}}, {
      attributeEntityPath: entityPath,
    });

    if (trigger.type === "state") {
      replaceWithNativeSelector(this, `${base}.from`, {state:{}}, {
        entityPath,
        attributePath: `${base}.attribute`,
      });
      replaceWithNativeSelector(this, `${base}.to`, {state:{}}, {
        entityPath,
        attributePath: `${base}.attribute`,
      });
    } else {
      const thresholdGrid = selectorForPath(root, `${base}.below`)?.closest?.(".grid");
      ensureInsertedInput(root, definition, {
        path: `${base}.for`,
        label: "Minimum duration (seconds)",
        anchor: thresholdGrid,
        type: "number",
        min: 0,
      });
    }

    if (selectorForPath(root, `${base}.for`)) {
      replaceWithDurationSelector(this, `${base}.for`, "Minimum duration");
    }
  }

  for (const [index, condition] of (definition.conditions || []).entries()) {
    const base = `conditions.${index}`;
    if (["state", "numeric_state"].includes(condition.type)) {
      replaceWithNativeSelector(this, `${base}.attribute`, {attribute:{}}, {
        attributeEntityPath: `${base}.entity_id`,
      });
    }
    if (condition.type === "state") {
      replaceWithNativeSelector(this, `${base}.state`, {state:{}}, {
        required: true,
        entityPath: `${base}.entity_id`,
        attributePath: `${base}.attribute`,
      });
    }
    if (condition.type === "time") {
      replaceWithNativeSelector(this, `${base}.after`, {time:{no_second:true}});
      replaceWithNativeSelector(this, `${base}.before`, {time:{no_second:true}});
    }
  }

  if (definition.active_window) {
    replaceWithNativeSelector(this, "active_window.start", {time:{no_second:true}}, {required:true});
    replaceWithNativeSelector(this, "active_window.end", {time:{no_second:true}}, {required:true});
  }

  if (definition.resolve_when?.type === "state") {
    replaceWithNativeSelector(this, "resolve_when.to", {state:{}}, {
      required: true,
      entityPath: "resolve_when.entity_id",
      attributePath: "resolve_when.attribute",
    });
  }

  for (const [path, label] of [
    ["cooldown", "Cooldown"],
    ["debounce", "Debounce"],
    ["match_window", "Correlation window"],
  ]) {
    if (selectorForPath(root, path)) replaceWithDurationSelector(this, path, label);
  }

  this.syncNativeSelectorContexts();
};

panel.scheduleNativeSelectorUpgrade = function() {
  if (nativeSelectorsAvailable() || this._nativeSelectorWaitScheduled) return;
  this._nativeSelectorWaitScheduled = true;
  ensureNativeSelectorsLoaded().then(available => {
    this._nativeSelectorWaitScheduled = false;
    if (available && this.editor) this.render();
  });
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  const helpers = this.shadowRoot?.querySelectorAll(".delivery-help") || [];
  if (helpers.length > 1) helpers[0].remove();
  this.hydrateNativeSelectors();
  this.scheduleNativeSelectorUpgrade();
};

panel.bind = function() {
  originalBind.call(this);
  if (!this.editor || !this.shadowRoot) return;

  this.shadowRoot.querySelectorAll("ha-selector[data-native-path]").forEach(selector => {
    selector.addEventListener("value-changed", event => {
      const changed = this.applyNativeSelectorValue(
        selector.dataset.nativePath,
        event.detail?.value ?? selector.value,
      );
      if (changed) queueMicrotask(() => this.syncNativeSelectorContexts());
    });
  });

  this.shadowRoot.querySelectorAll("ha-selector[data-native-duration-path]").forEach(selector => {
    selector.addEventListener("value-changed", event => {
      this.applyNativeSelectorValue(
        selector.dataset.nativeDurationPath,
        event.detail?.value ?? selector.value,
        "duration",
      );
    });
  });

  this.shadowRoot.querySelectorAll("[data-native-context-source]").forEach(control => {
    const refresh = () => queueMicrotask(() => this.syncNativeSelectorContexts());
    control.addEventListener("value-changed", refresh);
    control.addEventListener("change", refresh);
  });
};

panel.save = async function() {
  const definition = this.editor.definition;
  this.errors = this.validate(definition);
  if (Object.keys(this.errors).length) {
    this.showToast("Check the highlighted fields");
    this.render();
    return;
  }

  try {
    const editing = Boolean(this.editor.id);
    if (editing) {
      await this.hass.callWS({
        type: "conditional_notifications/update",
        notification_id: this.editor.id,
        changes: definition,
        expected_revision: this.editor.original?.revision,
      });
    } else {
      await this.hass.callWS({
        type: "conditional_notifications/create",
        definition,
      });
    }
    this.dirty = false;
    this.closeEditor(true);
    this.showToast(editing ? "Changes saved" : "Notification created");
    await this.refresh();
  } catch (error) {
    this.showToast(error.message || String(error));
  }
};

export { ConditionalNotificationsPanel };
