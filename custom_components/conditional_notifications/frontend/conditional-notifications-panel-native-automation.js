import { ConditionalNotificationsPanel } from "./conditional-notifications-panel-entry.js";

const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;
const originalOpenEditor = panel.openEditor;
const originalBind = panel.bind;
const originalValidate = panel.validate;
const originalSave = panel.save;
const originalTriggerSummary = panel.triggerSummary;
const originalConditionSummary = panel.conditionSummary;
const originalStyles = panel.styles;

const WEEKDAY_SHORT = {
  monday: "mon",
  tuesday: "tue",
  wednesday: "wed",
  thursday: "thu",
  friday: "fri",
  saturday: "sat",
  sunday: "sun",
};

const clone = value => value === undefined ? undefined : structuredClone(value);
const isNamedTrigger = trigger => trigger?.type === "named" && !trigger?.trigger && !trigger?.platform;
const isNativeTrigger = trigger => Boolean(trigger && (trigger.trigger || trigger.platform || trigger.triggers));
const isNativeCondition = condition => Boolean(condition && condition.condition);

function toNativeTrigger(trigger) {
  if (!trigger || typeof trigger !== "object") return trigger;
  if (isNativeTrigger(trigger)) return clone(trigger);
  if (trigger.type === "named") return null;
  if (trigger.type === "state") {
    const value = {trigger:"state", entity_id:trigger.entity_id};
    for (const key of ["from", "to", "attribute", "for"]) {
      if (trigger[key] !== undefined) value[key] = clone(trigger[key]);
    }
    return value;
  }
  if (trigger.type === "numeric_state") {
    const value = {trigger:"numeric_state", entity_id:trigger.entity_id};
    for (const key of ["above", "below", "attribute", "for"]) {
      if (trigger[key] !== undefined) value[key] = clone(trigger[key]);
    }
    return value;
  }
  if (trigger.type === "zone") {
    return {
      trigger:"zone",
      entity_id:trigger.entity_id,
      zone:trigger.zone_entity_id,
      event:trigger.event,
    };
  }
  if (trigger.type === "event") {
    const value = {trigger:"event", event_type:trigger.event_type};
    if (trigger.event_data !== undefined) value.event_data = clone(trigger.event_data);
    return value;
  }
  return null;
}

function toNativeCondition(condition) {
  if (!condition || typeof condition !== "object") return condition;
  if (isNativeCondition(condition)) return clone(condition);
  if (condition.type === "state") {
    const state = {
      condition:"state",
      entity_id:condition.entity_id,
      state:clone(condition.state),
    };
    if (condition.attribute !== undefined) state.attribute = condition.attribute;
    return condition.negate
      ? {condition:"not", conditions:[state]}
      : state;
  }
  if (condition.type === "numeric_state") {
    const value = {condition:"numeric_state", entity_id:condition.entity_id};
    for (const key of ["above", "below", "attribute"]) {
      if (condition[key] !== undefined) value[key] = clone(condition[key]);
    }
    return value;
  }
  if (condition.type === "zone") {
    return {
      condition:"zone",
      entity_id:condition.entity_id,
      zone:condition.zone_entity_id,
    };
  }
  if (condition.type === "time") {
    const value = {condition:"time"};
    if (condition.after) value.after = condition.after;
    if (condition.before) value.before = condition.before;
    if (condition.weekdays?.length) {
      value.weekday = condition.weekdays.map(day => WEEKDAY_SHORT[day] || day);
    }
    return value;
  }
  return condition;
}

function nativeTriggers(definition) {
  return (definition.triggers || []).filter(trigger => !isNamedTrigger(trigger)).map(toNativeTrigger).filter(Boolean);
}

function nativeConditions(definition) {
  return (definition.conditions || []).map(toNativeCondition).filter(Boolean);
}

function mergeNativeTriggers(existing, replacement) {
  const remaining = replacement.map(clone);
  const merged = [];
  for (const trigger of existing || []) {
    if (isNamedTrigger(trigger)) {
      merged.push(trigger);
    } else if (remaining.length) {
      merged.push(remaining.shift());
    }
  }
  merged.push(...remaining);
  return merged;
}

function sectionByHeading(root, heading) {
  return [...(root?.querySelectorAll(".editor-body > section") || [])]
    .find(section => section.querySelector("h3")?.textContent?.trim() === heading);
}

function advancedGroupByHeading(root, heading) {
  return [...(root?.querySelectorAll(".advanced-group") || [])]
    .find(group => group.querySelector("h4")?.textContent?.trim() === heading);
}

function makeSelector(instance, selector, value, id) {
  const element = document.createElement("ha-selector");
  element.id = id;
  element.className = "native-automation-selector";
  element.hass = instance.hass;
  // The HA automation editor is embedded in a bounded modal, so use its
  // compact layout even when the overall Home Assistant viewport is wide.
  element.narrow = true;
  element.selector = selector;
  element.value = clone(value);
  element.required = false;
  return element;
}

function syncNativeAutomationSelectorValue(selector, value) {
  if (!selector) return;
  // HA's trigger/condition selectors are controlled components: the inner
  // automation editor emits the new value but does not commit it to the
  // selector host. Feed it back immediately so additions/removals render
  // without waiting for some unrelated outer-panel rerender.
  selector.value = clone(value);
}

function ensureNativeAutomationTranslations(instance) {
  if (instance._nativeAutomationTranslationsLoaded) return Promise.resolve();
  if (instance._nativeAutomationTranslationsPromise) {
    return instance._nativeAutomationTranslationsPromise;
  }

  const loader = instance.hass?.loadFragmentTranslation;
  if (typeof loader !== "function") {
    instance._nativeAutomationTranslationsLoaded = true;
    return Promise.resolve();
  }

  let loadResult;
  try {
    loadResult = loader.call(instance.hass, "config");
  } catch (error) {
    console.warn("Unable to load Home Assistant automation translations", error);
    return Promise.resolve();
  }

  let promise;
  promise = Promise.resolve(loadResult)
    .then(() => {
      instance._nativeAutomationTranslationsLoaded = true;
    })
    .catch(error => {
      console.warn("Unable to load Home Assistant automation translations", error);
    })
    .finally(() => {
      if (instance._nativeAutomationTranslationsPromise === promise) {
        instance._nativeAutomationTranslationsPromise = undefined;
      }
    });
  instance._nativeAutomationTranslationsPromise = promise;
  return promise;
}

const NATIVE_AUTOMATION_WIDTH_HOSTS = new Set([
  "ha-selector-trigger",
  "ha-selector-condition",
  "ha-automation-trigger",
  "ha-automation-condition",
  "ha-sortable",
  "ha-automation-trigger-row",
  "ha-automation-condition-row",
  "ha-card",
  "ha-expansion-panel",
  "ha-automation-trigger-editor",
  "ha-automation-condition-editor",
  "ha-form",
]);

const NATIVE_AUTOMATION_NEGATIVE_MARGIN_HOSTS = new Set([
  "ha-automation-trigger-platform",
  "ha-automation-condition-platform",
]);

function constrainNativeAutomationTree(root) {
  if (!root?.querySelectorAll) return;
  for (const element of root.querySelectorAll("*")) {
    if (NATIVE_AUTOMATION_WIDTH_HOSTS.has(element.localName)) {
      element.style.minWidth = "0";
      element.style.maxWidth = "100%";
      element.style.width = "100%";
      element.style.boxSizing = "border-box";
    }
    // Home Assistant's integration-provided trigger/condition platform editors
    // deliberately bleed one spacing unit into the surrounding automation
    // editor gutter. Inside our bounded card that negative inline margin is
    // outside the available width, so neutralize only those two host margins.
    if (NATIVE_AUTOMATION_NEGATIVE_MARGIN_HOSTS.has(element.localName)) {
      element.style.marginInline = "0";
    }
    if (element.shadowRoot) constrainNativeAutomationTree(element.shadowRoot);
  }
}

function normalizeNativeAutomationLayout(selector) {
  if (!selector) return;
  selector.style.minWidth = "0";
  selector.style.maxWidth = "100%";
  selector.style.width = "100%";
  selector.style.boxSizing = "border-box";

  const apply = () => constrainNativeAutomationTree(selector.shadowRoot);
  queueMicrotask(apply);
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      apply();
      requestAnimationFrame(apply);
    });
  }
}

function entityLabel(instance, entityId) {
  if (!entityId) return "an entity";
  const state = instance.hass?.states?.[entityId];
  return state?.attributes?.friendly_name || entityId;
}

function simpleCurrentStateCandidate(trigger) {
  const native = toNativeTrigger(trigger);
  return Boolean(
    native
    && (native.trigger === "state" || native.platform === "state")
    && typeof native.entity_id === "string"
    && native.to !== undefined
    && !Array.isArray(native.to)
  );
}

function createExternalTriggerRow(instance, trigger, originalIndex) {
  const row = document.createElement("div");
  row.className = "external-trigger-row";
  const label = document.createElement("label");
  label.append(document.createTextNode("External trigger name"));
  const input = document.createElement("input");
  input.value = trigger.trigger_id || "";
  input.placeholder = "door_attention_needed";
  input.dataset.externalTriggerIndex = String(originalIndex);
  label.append(input);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon danger";
  remove.dataset.removeExternalTrigger = String(originalIndex);
  remove.setAttribute("aria-label", "Remove external trigger");
  remove.textContent = "×";
  row.append(label, remove);
  return row;
}

panel.styles = function() {
  return originalStyles.call(this).replace("</style>", `
    .native-automation-editor{margin:12px 0 4px;padding-inline:8px;min-width:0;max-width:100%;width:100%;box-sizing:border-box;contain:inline-size;overflow-x:auto;overscroll-behavior-inline:contain}
    .native-automation-selector{display:block;width:100%;max-width:100%;min-width:0;box-sizing:border-box;contain:inline-size}
    .native-editor-help{display:block;color:var(--secondary-text-color);font-size:13px;line-height:1.45;margin:4px 0 12px}
    .external-trigger-box{margin-top:16px;padding-top:14px;border-top:1px solid var(--divider-color)}
    .external-trigger-box>strong{display:block;margin-bottom:3px}
    .external-trigger-row{display:grid;grid-template-columns:1fr auto;align-items:end;gap:8px}
    .external-trigger-row label{margin:8px 0}
    .native-resolution-error{color:var(--error-color,#db4437);font-size:12px;margin:6px 0}
    @media(max-width:700px){.external-trigger-row{grid-template-columns:1fr auto}}
  </style>`);
};

panel.triggerSummary = function(trigger) {
  if (!isNativeTrigger(trigger)) return originalTriggerSummary.call(this, trigger);
  if (trigger.triggers) return "a Home Assistant trigger group fires";
  const kind = trigger.trigger || trigger.platform || "trigger";
  const entity = Array.isArray(trigger.entity_id) ? trigger.entity_id.join(", ") : trigger.entity_id;
  if (kind === "state") {
    return `${entityLabel(this, entity)} changes${trigger.to !== undefined ? ` to ${Array.isArray(trigger.to) ? trigger.to.join(" or ") : trigger.to}` : ""}`;
  }
  if (kind === "numeric_state") return `${entityLabel(this, entity)} crosses its configured numeric threshold`;
  if (kind === "zone") return `${entityLabel(this, entity)} ${trigger.event || "changes zone at"} ${entityLabel(this, trigger.zone)}`;
  if (kind === "event") return `event ${Array.isArray(trigger.event_type) ? trigger.event_type.join(", ") : trigger.event_type || "(configured event)"} fires`;
  if (kind === "time") return `the configured time ${typeof trigger.at === "string" ? trigger.at : "arrives"}`;
  if (kind === "calendar") return `${entityLabel(this, trigger.entity_id)} calendar event ${trigger.event || "changes"}`;
  if (kind === "sun") return `${trigger.event || "sun"} trigger fires`;
  return `Home Assistant ${String(kind).replaceAll("_", " ")} trigger fires`;
};

panel.conditionSummary = function(condition) {
  if (!isNativeCondition(condition)) return originalConditionSummary.call(this, condition);
  const kind = condition.condition;
  if (kind === "state") return `${entityLabel(this, condition.entity_id)} is ${Array.isArray(condition.state) ? condition.state.join(" or ") : condition.state}`;
  if (kind === "numeric_state") return `${entityLabel(this, condition.entity_id)} is within its configured numeric bounds`;
  if (kind === "zone") return `${entityLabel(this, condition.entity_id)} is in ${entityLabel(this, condition.zone)}`;
  if (kind === "time") return "the configured Home Assistant time condition passes";
  if (["and", "or", "not"].includes(kind)) return `${kind.toUpperCase()} condition group passes`;
  return `Home Assistant ${String(kind).replaceAll("_", " ")} condition passes`;
};

panel.hydrateNativeAutomationEditors = function() {
  if (!this.editor || !this.shadowRoot || !customElements.get?.("ha-selector")) return;
  const definition = this.editor.definition;

  const when = sectionByHeading(this.shadowRoot, "When");
  if (when && !when.querySelector("#native-ha-triggers")) {
    const heading = when.querySelector("h3");
    const help = when.querySelector(".section-help");
    [...when.children].forEach(child => {
      if (child !== heading && child !== help) child.remove();
    });

    const editor = document.createElement("div");
    editor.className = "native-automation-editor";
    editor.append(makeSelector(this, {trigger:{}}, nativeTriggers(definition), "native-ha-triggers"));
    const note = document.createElement("small");
    note.className = "native-editor-help";
    note.textContent = "Uses Home Assistant's full trigger editor. Trigger groups count as one Conditional Notifications signal for correlation.";
    editor.append(note);
    when.append(editor);

    const external = document.createElement("div");
    external.className = "external-trigger-box";
    const title = document.createElement("strong");
    title.textContent = "External / integration triggers";
    const description = document.createElement("small");
    description.className = "native-editor-help";
    description.textContent = "Optional named signals that another integration, automation, service, or LLM tool can fire directly.";
    external.append(title, description);
    (definition.triggers || []).forEach((trigger, index) => {
      if (isNamedTrigger(trigger)) external.append(createExternalTriggerRow(this, trigger, index));
    });
    const add = document.createElement("button");
    add.type = "button";
    add.className = "secondary";
    add.id = "add-external-trigger";
    add.textContent = "+ Add external trigger";
    external.append(add);
    when.append(external);
  }

  const onlyIf = sectionByHeading(this.shadowRoot, "Only if");
  if (onlyIf && !onlyIf.querySelector("#native-ha-conditions")) {
    const heading = onlyIf.querySelector("h3");
    const help = onlyIf.querySelector(".section-help");
    [...onlyIf.children].forEach(child => {
      if (child !== heading && child !== help) child.remove();
    });
    const editor = document.createElement("div");
    editor.className = "native-automation-editor";
    editor.append(makeSelector(
      this,
      {condition:{optionsInSidebar:false}},
      nativeConditions(definition),
      "native-ha-conditions",
    ));
    const note = document.createElement("small");
    note.className = "native-editor-help";
    note.textContent = "Uses Home Assistant's full condition editor, including AND, OR, NOT, template, device, sun, and integration conditions.";
    editor.append(note);
    onlyIf.append(editor);
  }

  const resolution = advancedGroupByHeading(this.shadowRoot, "Resolution");
  if (resolution && !resolution.querySelector("#native-resolve-toggle")) {
    resolution.replaceChildren();
    const heading = document.createElement("h4");
    heading.textContent = "Resolution";
    const toggleLabel = document.createElement("label");
    toggleLabel.className = "check";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.id = "native-resolve-toggle";
    toggle.checked = Boolean(definition.resolve_when);
    toggleLabel.append(toggle, document.createTextNode(" Auto-resolve when another trigger fires"));
    resolution.append(heading, toggleLabel);

    if (definition.resolve_when) {
      const named = isNamedTrigger(definition.resolve_when);
      const modeLabel = document.createElement("label");
      modeLabel.append(document.createTextNode("Resolution source"));
      const mode = document.createElement("select");
      mode.id = "native-resolution-mode";
      for (const [value, text] of [["ha", "Home Assistant trigger"], ["external", "External / integration trigger"]]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        option.selected = named ? value === "external" : value === "ha";
        mode.append(option);
      }
      modeLabel.append(mode);
      resolution.append(modeLabel);

      if (named) {
        const label = document.createElement("label");
        label.append(document.createTextNode("External trigger name"));
        const input = document.createElement("input");
        input.id = "native-resolution-external-name";
        input.value = definition.resolve_when.trigger_id || "";
        label.append(input);
        resolution.append(label);
      } else {
        const native = toNativeTrigger(definition.resolve_when);
        const selector = makeSelector(
          this,
          {trigger:{}},
          this._nativeResolutionDraft || (native ? [native] : []),
          "native-ha-resolution",
        );
        resolution.append(selector);
        const note = document.createElement("small");
        note.className = "native-editor-help";
        note.textContent = "Choose exactly one trigger. State-like triggers are re-checked after delivery; one-shot triggers are retained if they fire while delivery is still in progress.";
        resolution.append(note);
        if (this._nativeResolutionError) {
          const error = document.createElement("div");
          error.className = "native-resolution-error";
          error.textContent = this._nativeResolutionError;
          resolution.append(error);
        }
      }

      const clear = document.createElement("label");
      clear.className = "check";
      const clearInput = document.createElement("input");
      clearInput.type = "checkbox";
      clearInput.id = "native-clear-on-resolve";
      clearInput.checked = definition.clear_on_resolve !== false;
      clear.append(clearInput, document.createTextNode(" Clear the tagged persistent notification when resolved"));
      resolution.append(clear);
    }
  }

  this.shadowRoot.querySelectorAll(".native-automation-selector")
    .forEach(normalizeNativeAutomationLayout);

  const matchCurrent = this.shadowRoot.querySelector('[data-path="match_current_state"]');
  if (matchCurrent) {
    const supported = (definition.triggers || []).some(simpleCurrentStateCandidate);
    matchCurrent.disabled = !supported;
    const label = matchCurrent.closest("label");
    if (label) label.style.display = supported ? "" : "none";
  }
};

panel.openEditor = function(record) {
  const open = () => originalOpenEditor.call(this, record);
  if (!this.hass?.loadFragmentTranslation) return open();
  return ensureNativeAutomationTranslations(this).then(open);
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  this.hydrateNativeAutomationEditors();
};

panel.bind = function() {
  originalBind.call(this);
  if (!this.editor || !this.shadowRoot || !customElements.get?.("ha-selector")) return;
  const definition = this.editor.definition;

  this.shadowRoot.querySelector("#native-ha-triggers")?.addEventListener("value-changed", event => {
    const value = event.detail?.value ?? event.currentTarget.value ?? [];
    const replacement = Array.isArray(value) ? value : [value];
    definition.triggers = mergeNativeTriggers(definition.triggers, replacement);
    syncNativeAutomationSelectorValue(event.currentTarget, replacement);
    normalizeNativeAutomationLayout(event.currentTarget);
    if (!(definition.triggers || []).some(simpleCurrentStateCandidate)) {
      delete definition.match_current_state;
    }
    this.markDirty();
    this.updatePreview();
  });

  this.shadowRoot.querySelector("#native-ha-conditions")?.addEventListener("value-changed", event => {
    const value = event.detail?.value ?? event.currentTarget.value ?? [];
    definition.conditions = clone(Array.isArray(value) ? value : [value]);
    syncNativeAutomationSelectorValue(event.currentTarget, definition.conditions);
    normalizeNativeAutomationLayout(event.currentTarget);
    this.markDirty();
    this.updatePreview();
  });

  this.shadowRoot.querySelectorAll("[data-external-trigger-index]").forEach(input => {
    input.addEventListener("input", event => {
      const index = Number(event.currentTarget.dataset.externalTriggerIndex);
      definition.triggers[index].trigger_id = event.currentTarget.value.trim();
      this.markDirty();
      this.updatePreview();
    });
  });
  this.shadowRoot.querySelectorAll("[data-remove-external-trigger]").forEach(button => {
    button.addEventListener("click", event => {
      definition.triggers.splice(Number(event.currentTarget.dataset.removeExternalTrigger), 1);
      this.markDirty();
      this.render();
    });
  });
  this.shadowRoot.querySelector("#add-external-trigger")?.addEventListener("click", () => {
    definition.triggers.push({type:"named", trigger_id:""});
    this.markDirty();
    this.render();
  });

  this.shadowRoot.querySelector("#native-resolve-toggle")?.addEventListener("change", event => {
    if (event.currentTarget.checked) {
      definition.resolve_when = {trigger:"state", entity_id:"", to:"off"};
      this._nativeResolutionDraft = undefined;
      this._nativeResolutionError = undefined;
    } else {
      delete definition.resolve_when;
      this._nativeResolutionDraft = undefined;
      this._nativeResolutionError = undefined;
    }
    this.markDirty();
    this.render();
  });

  this.shadowRoot.querySelector("#native-resolution-mode")?.addEventListener("change", event => {
    if (event.currentTarget.value === "external") {
      definition.resolve_when = {type:"named", trigger_id:""};
    } else {
      definition.resolve_when = {trigger:"state", entity_id:"", to:"off"};
    }
    this._nativeResolutionDraft = undefined;
    this._nativeResolutionError = undefined;
    this.markDirty();
    this.render();
  });

  this.shadowRoot.querySelector("#native-resolution-external-name")?.addEventListener("input", event => {
    definition.resolve_when.trigger_id = event.currentTarget.value.trim();
    this.markDirty();
    this.updatePreview();
  });

  this.shadowRoot.querySelector("#native-ha-resolution")?.addEventListener("value-changed", event => {
    const value = event.detail?.value ?? event.currentTarget.value ?? [];
    const triggers = clone(Array.isArray(value) ? value : [value]);
    this._nativeResolutionDraft = triggers;
    syncNativeAutomationSelectorValue(event.currentTarget, triggers);
    normalizeNativeAutomationLayout(event.currentTarget);
    if (triggers.length === 1) {
      definition.resolve_when = triggers[0];
      this._nativeResolutionError = undefined;
    } else {
      this._nativeResolutionError = "Resolution needs exactly one Home Assistant trigger.";
    }
    this.markDirty();
    this.updatePreview();
  });

  this.shadowRoot.querySelector("#native-clear-on-resolve")?.addEventListener("change", event => {
    definition.clear_on_resolve = event.currentTarget.checked;
    this.markDirty();
    this.updatePreview();
  });
};

panel.validate = function(definition) {
  const errors = originalValidate.call(this, definition);
  if (!definition.triggers?.length) errors.triggers = "Add at least one trigger.";
  definition.triggers?.forEach((trigger, index) => {
    if (isNamedTrigger(trigger) && !trigger.trigger_id?.trim()) {
      errors[`trigger${index}`] = "Give each external trigger a name.";
    }
  });
  if (isNamedTrigger(definition.resolve_when) && !definition.resolve_when.trigger_id?.trim()) {
    errors.resolution = "Give the external resolution trigger a name.";
  }
  if (this._nativeResolutionDraft && this._nativeResolutionDraft.length !== 1) {
    errors.resolution = "Resolution needs exactly one Home Assistant trigger.";
  }
  return errors;
};

panel.save = async function() {
  if (!(this.editor?.definition?.triggers || []).some(simpleCurrentStateCandidate)) {
    delete this.editor?.definition?.match_current_state;
  }
  return originalSave.call(this);
};

export {
  ConditionalNotificationsPanel,
  constrainNativeAutomationTree,
  ensureNativeAutomationTranslations,
  mergeNativeTriggers,
  normalizeNativeAutomationLayout,
  simpleCurrentStateCandidate,
  syncNativeAutomationSelectorValue,
  toNativeCondition,
  toNativeTrigger,
};
