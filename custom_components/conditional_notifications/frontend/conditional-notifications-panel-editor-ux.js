const nativeAutomationUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-native-automation.js"
  : "/conditional_notifications_panel_native_automation.js";

const { ConditionalNotificationsPanel } = await import(nativeAutomationUrl);

const panel = ConditionalNotificationsPanel.prototype;
const originalBind = panel.bind;
const originalHydrateEditor = panel.hydrateEditor;
const originalOpenEditor = panel.openEditor;
const originalSave = panel.save;
const originalStyles = panel.styles;

const ADVANCED_HEADINGS = new Map([
  ["Schedule & expiry", "schedule"],
  ["Timing protection", "timing"],
  ["Recurring hours", "recurring"],
  ["Resolution", "resolution"],
  ["Multiple triggers", "correlation"],
  ["Companion App", "companion"],
]);

function errorLocation(key) {
  if (key === "name") return {section:"Basics"};
  if (key === "triggers" || key.startsWith("trigger")) return {section:"When"};
  if (key === "conditions" || key.startsWith("condition")) return {section:"Only if"};
  if (["title", "message", "delivery", "companion"].includes(key)) {
    return key === "companion"
      ? {section:"More options", optionKey:"companion"}
      : {section:"Send"};
  }
  if (key === "repeat") return {section:"After notifying"};
  if (["available_from", "expires_at"].includes(key)) {
    return {section:"More options", optionKey:"schedule"};
  }
  if (["cooldown", "debounce", "match_current_state"].includes(key)) {
    return {section:"More options", optionKey:"timing"};
  }
  if (key === "active_window") return {section:"More options", optionKey:"recurring"};
  if (key === "resolve_when") return {section:"More options", optionKey:"resolution"};
  if (["match", "match_window"].includes(key)) {
    return {section:"More options", optionKey:"correlation"};
  }
  return {section:"Review"};
}

function editorErrorItems(errors = {}) {
  return Object.entries(errors)
    .filter(([, message]) => Boolean(message))
    .map(([key, message]) => ({key, message:String(message), ...errorLocation(key)}));
}

function activeAdvancedOptions(definition = {}) {
  const options = [];
  if (definition.available_from || definition.expires_at || definition.notify_on_expiry) {
    options.push({key:"schedule", label:"Schedule"});
  }
  if (definition.cooldown || definition.debounce || definition.match_current_state) {
    options.push({key:"timing", label:"Timing"});
  }
  if (definition.active_window) options.push({key:"recurring", label:"Hours"});
  if (definition.resolve_when) options.push({key:"resolution", label:"Resolution"});
  if (definition.match === "all_within") {
    options.push({key:"correlation", label:"Correlation"});
  }
  if (definition.delivery?.companion?.url || definition.delivery?.companion?.actions?.length) {
    options.push({key:"companion", label:"Companion App"});
  }
  return options;
}

function makeTextElement(name, className, text) {
  const element = document.createElement(name);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function stepSection(root, title) {
  return [...(root.querySelectorAll(".editor-body > section") || [])]
    .find(section => section.dataset.editorSection === title);
}

panel.styles = function() {
  return originalStyles.call(this).replace("</style>", `
    .editor-body{padding:0 24px 30px}
    .editor-body>.editor-step{margin:16px 0;padding:20px!important;border:1px solid color-mix(in srgb,var(--divider-color) 86%,transparent)!important;border-radius:16px;background:color-mix(in srgb,var(--secondary-background-color) 34%,var(--card-background-color))}
    .editor-body>.editor-step:first-of-type{margin-top:20px}
    .editor-step h3{margin-bottom:6px}
    .editor-step.has-error{border-color:var(--error-color,#db4437)!important;box-shadow:0 0 0 1px color-mix(in srgb,var(--error-color,#db4437) 22%,transparent)}
    .editor-review{margin:16px 0 14px!important;padding:18px 20px!important;border-radius:16px!important;background:color-mix(in srgb,var(--primary-color) 6%,var(--card-background-color))!important}
    .editor-review h3{font-size:18px!important;margin-bottom:4px!important}
    .editor-review .section-help{margin:0 0 12px}
    .editor-error-summary{margin:18px 0 4px;padding:15px 17px;border:1px solid color-mix(in srgb,var(--error-color,#db4437) 58%,var(--divider-color));border-radius:14px;background:color-mix(in srgb,var(--error-color,#db4437) 7%,var(--card-background-color));outline:none}
    .editor-error-summary strong{display:block;margin-bottom:7px}
    .editor-error-summary ul{margin:0;padding-left:20px}
    .editor-error-summary li{margin:4px 0}
    .editor-error-summary button{border:0;background:none;padding:0;color:var(--error-color,#db4437);font:inherit;text-align:left;text-decoration:underline;text-underline-offset:2px}
    .more-options{margin:4px 0 12px;padding:0!important;border:1px solid var(--divider-color)!important;border-radius:16px;background:color-mix(in srgb,var(--secondary-background-color) 24%,var(--card-background-color));overflow:hidden}
    .more-options>summary{display:flex;align-items:center;gap:10px;min-height:54px;padding:14px 18px!important;font-size:16px;list-style-position:inside}
    .more-options[open]>summary{border-bottom:1px solid var(--divider-color)}
    .more-options-title{font-weight:650}
    .more-options-active{display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-left:auto}
    .option-chip{display:inline-flex;align-items:center;min-height:24px;padding:2px 8px;border-radius:999px;background:var(--secondary-background-color);color:var(--secondary-text-color);font-size:12px;font-weight:500}
    .more-options-hint{margin-left:auto;color:var(--secondary-text-color);font-size:13px;font-weight:400}
    .more-options .advanced{padding:0 18px 6px}
    .advanced-disclosure{padding:0!important;border:0!important;border-top:1px solid var(--divider-color)!important}
    .advanced-disclosure:first-child{border-top:0!important}
    .advanced-disclosure>summary{display:flex;align-items:center;gap:10px;padding:15px 2px;font-weight:600}
    .advanced-disclosure[open]>summary{padding-bottom:10px}
    .advanced-disclosure-body{padding:0 2px 16px}
    .advanced-option-state{margin-left:auto;color:var(--secondary-text-color);font-size:12px;font-weight:500}
    .advanced-option-state.configured{color:var(--primary-color)}
    .external-trigger-box:not(.has-external){margin-top:9px;padding-top:0;border-top:0}
    .external-trigger-box:not(.has-external)>strong,.external-trigger-box:not(.has-external)>.native-editor-help{display:none}
    .external-trigger-box:not(.has-external)>#add-external-trigger{margin-top:0}
    .editor-step:focus,.editor-review:focus,.more-options:focus,.advanced-disclosure:focus{outline:2px solid var(--primary-color);outline-offset:2px}
    @media(max-width:700px){
      .editor-body{padding:0 14px 22px}
      .editor-body>.editor-step{margin:12px 0;padding:16px!important;border-radius:14px}
      .editor-review{margin:12px 0!important;padding:16px!important}
      .more-options{border-radius:14px}
      .more-options>summary{align-items:flex-start;flex-wrap:wrap;padding:13px 15px!important}
      .more-options-active,.more-options-hint{width:100%;margin-left:0;padding-left:20px}
      .more-options .advanced{padding:0 15px 5px}
    }
  </style>`);
};

panel.openEditor = function(record) {
  this._editorUxOpenOptions = new Set();
  return originalOpenEditor.call(this, record);
};

panel.focusEditorSection = function(sectionName, optionKey) {
  const root = this.shadowRoot;
  if (!root) return;

  let target;
  if (sectionName === "More options") {
    const more = root.querySelector(".more-options");
    if (!more) return;
    more.open = true;
    this.advancedOpen = true;
    target = more;
    if (optionKey) {
      this._editorUxOpenOptions ??= new Set();
      this._editorUxOpenOptions.add(optionKey);
      const option = [...more.querySelectorAll(".advanced-disclosure")]
        .find(details => details.dataset.optionKey === optionKey);
      if (option) {
        option.open = true;
        target = option;
      }
    }
  } else if (sectionName === "Review") {
    target = root.querySelector(".editor-review");
  } else {
    target = stepSection(root, sectionName);
  }

  target?.scrollIntoView?.({behavior:"smooth", block:"start"});
  target?.focus?.({preventScroll:true});
};

panel.enhanceEditorUx = function() {
  if (!this.editor || !this.shadowRoot) return;
  const root = this.shadowRoot;
  const body = root.querySelector(".editor-body");
  if (!body) return;

  const dialog = root.querySelector(".dialog");
  const description = dialog?.querySelector("header p");
  if (dialog && description) {
    description.id = "editor-description";
    dialog.setAttribute("aria-describedby", "editor-description");
  }

  const stepTitles = ["Basics", "When", "Only if", "Send", "Then"];
  for (const title of stepTitles) {
    const section = [...body.querySelectorAll(":scope > section")]
      .find(candidate => candidate.querySelector("h3")?.textContent?.trim() === title);
    if (!section) continue;
    const displayTitle = title === "Then" ? "After notifying" : title;
    section.querySelector("h3").textContent = displayTitle;
    section.classList.add("editor-step");
    section.dataset.editorSection = displayTitle;
    section.tabIndex = -1;
  }

  const review = body.querySelector(":scope > .editor-summary");
  const more = body.querySelector(":scope > .more-options");
  if (review) {
    review.classList.add("editor-review");
    review.dataset.editorSection = "Review";
    review.tabIndex = -1;
    const heading = review.querySelector("h3");
    if (heading) {
      heading.textContent = "Review";
      if (!review.querySelector(".section-help")) {
        heading.insertAdjacentElement(
          "afterend",
          makeTextElement(
            "p",
            "section-help",
            "Check the plain-English summary before creating the notification.",
          ),
        );
      }
    }
    if (more) more.before(review);
  }

  const errors = editorErrorItems(this.errors);
  const advancedErrors = errors.filter(item => item.optionKey);
  if (advancedErrors.length) {
    this.advancedOpen = true;
    this._editorUxOpenOptions ??= new Set();
    for (const item of advancedErrors) this._editorUxOpenOptions.add(item.optionKey);
  }

  for (const item of errors) {
    if (item.section === "More options" || item.section === "Review") continue;
    stepSection(root, item.section)?.classList.add("has-error");
  }

  if (errors.length) {
    const summary = document.createElement("section");
    summary.className = "editor-error-summary";
    summary.setAttribute("role", "alert");
    summary.tabIndex = -1;
    summary.append(makeTextElement("strong", "", "Check these settings before saving"));
    const list = document.createElement("ul");
    for (const item of errors) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.errorSection = item.section;
      if (item.optionKey) button.dataset.errorOption = item.optionKey;
      button.textContent = `${item.section}: ${item.message}`;
      row.append(button);
      list.append(row);
    }
    summary.append(list);
    body.prepend(summary);
  }

  if (more) {
    more.tabIndex = -1;
    const active = activeAdvancedOptions(this.editor.definition);
    const activeKeys = new Set(active.map(item => item.key));
    const summary = more.querySelector(":scope > summary");
    if (summary) {
      summary.replaceChildren(makeTextElement("span", "more-options-title", "More options"));
      if (active.length) {
        const badges = document.createElement("span");
        badges.className = "more-options-active";
        for (const item of active.slice(0, 3)) {
          badges.append(makeTextElement("span", "option-chip", item.label));
        }
        if (active.length > 3) {
          badges.append(makeTextElement("span", "option-chip", `+${active.length - 3}`));
        }
        summary.append(badges);
      } else {
        summary.append(makeTextElement("span", "more-options-hint", "Optional"));
      }
    }

    const groups = [...more.querySelectorAll(".advanced > .advanced-group")];
    for (const group of groups) {
      const heading = group.querySelector(":scope > h4");
      if (!heading) continue;
      const optionKey = ADVANCED_HEADINGS.get(heading.textContent.trim());
      if (!optionKey) continue;

      const details = document.createElement("details");
      details.className = `${group.className} advanced-disclosure`;
      details.dataset.optionKey = optionKey;
      details.tabIndex = -1;
      details.open = Boolean(this._editorUxOpenOptions?.has(optionKey));

      const optionSummary = document.createElement("summary");
      optionSummary.append(makeTextElement("span", "", heading.textContent.trim()));
      optionSummary.append(makeTextElement(
        "span",
        `advanced-option-state${activeKeys.has(optionKey) ? " configured" : ""}`,
        activeKeys.has(optionKey) ? "Configured" : "Optional",
      ));

      const optionBody = document.createElement("div");
      optionBody.className = "advanced-disclosure-body";
      for (const child of [...group.children]) {
        if (child !== heading) optionBody.append(child);
      }
      details.append(optionSummary, optionBody);
      group.replaceWith(details);
    }
  }

  const external = root.querySelector(".external-trigger-box");
  if (external) {
    const hasExternal = Boolean(external.querySelector(".external-trigger-row"));
    external.classList.toggle("has-external", hasExternal);
    const add = external.querySelector("#add-external-trigger");
    if (add) {
      add.textContent = hasExternal
        ? "+ Add another external trigger"
        : "+ Add external / integration trigger";
    }
  }
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  this.enhanceEditorUx();
};

panel.bind = function() {
  originalBind.call(this);
  if (!this.editor || !this.shadowRoot) return;
  const root = this.shadowRoot;

  root.querySelectorAll("[data-error-section]").forEach(button => {
    button.addEventListener("click", () => {
      this.focusEditorSection(button.dataset.errorSection, button.dataset.errorOption);
    });
  });

  root.querySelectorAll(".advanced-disclosure[data-option-key]").forEach(details => {
    details.addEventListener("toggle", () => {
      this._editorUxOpenOptions ??= new Set();
      if (details.open) this._editorUxOpenOptions.add(details.dataset.optionKey);
      else this._editorUxOpenOptions.delete(details.dataset.optionKey);
    });
  });

  root.querySelector(".dialog")?.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    const path = event.composedPath?.() || [];
    if (path.some(node => ["ha-selector", "ha-entity-picker", "ha-dialog"].includes(node?.localName))) {
      return;
    }
    event.preventDefault();
    this.closeEditor();
  });
};

panel.save = async function() {
  const result = await originalSave.call(this);
  if (this.editor && Object.keys(this.errors || {}).length) {
    requestAnimationFrame(() => {
      const summary = this.shadowRoot?.querySelector(".editor-error-summary");
      summary?.scrollIntoView?.({behavior:"smooth", block:"start"});
      summary?.focus?.({preventScroll:true});
    });
  }
  return result;
};

export {
  ConditionalNotificationsPanel,
  activeAdvancedOptions,
  editorErrorItems,
};
