const statusUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-status.js"
  : "/conditional_notifications_panel_status.js";
const { ConditionalNotificationsPanel } = await import(statusUrl);

const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;
const originalBind = panel.bind;
const originalPreview = panel.preview;
const originalValidate = panel.validate;
const escLocal = (value) => String(value ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

function companion(definition) {
  definition.delivery ??= {use_defaults:true};
  return definition.delivery.companion ??= {};
}

function cleanupCompanion(definition) {
  const value = definition.delivery?.companion;
  if (!value) return;
  if (!value.url && !(value.actions || []).length) delete definition.delivery.companion;
}

panel.preview = function(definition) {
  const preview = originalPreview.call(this, definition);
  if (definition.match === "all_within") {
    const triggers = definition.triggers.map(trigger => this.triggerSummary(trigger)).join("; and ");
    preview.watching = `All configured triggers within ${this.duration(definition.match_window || 0)}: ${triggers}`;
  }
  const options = definition.delivery?.companion;
  if (options?.url || options?.actions?.length) {
    const parts = [];
    if (options.url) parts.push(`tap opens ${options.url}`);
    if (options.actions?.length) parts.push(`${options.actions.length} action button${options.actions.length === 1 ? "" : "s"}`);
    preview.delivery += `; Companion App: ${parts.join(", ")}`;
  }
  return preview;
};

panel.validate = function(definition) {
  const errors = originalValidate.call(this, definition);
  const options = definition.delivery?.companion;
  if (!options) return errors;
  const safeUri = (value) => value?.startsWith("/") || /^https?:\/\/[^\s]+$/i.test(value || "");
  if (options.url && !safeUri(options.url)) errors.companion = "Use a Home Assistant path beginning with / or an http/https URL.";
  if ((options.actions || []).length > 3) errors.companion = "Use at most three Companion App buttons.";
  for (const item of options.actions || []) {
    if (!item.title?.trim()) errors.companion = "Give each Companion App button a title.";
    if (item.uri && !safeUri(item.uri)) errors.companion = "Button links must be Home Assistant paths or http/https URLs.";
    if (item.action && !/^[A-Za-z0-9_:-]{1,64}$/.test(item.action)) errors.companion = "Button action IDs may use letters, numbers, _, :, and -.";
  }
  return errors;
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  if (!this.editor) return;

  const definition = this.editor.definition;
  const anchor = this.shadowRoot.querySelector(".preview");
  if (!anchor) return;

  if (!this.shadowRoot.querySelector("#trigger-match-mode")) {
    const correlated = definition.match === "all_within";
    const canCorrelate = definition.triggers.length >= 2;
    anchor.insertAdjacentHTML(
      "beforebegin",
      `<section class="subcard correlation-card">
        <div class="subhead"><strong>How multiple triggers combine</strong></div>
        <label>Match mode
          <select id="trigger-match-mode">
            <option value="any"${correlated ? "" : " selected"}>Any trigger can notify</option>
            <option value="all_within"${correlated ? " selected" : ""}${canCorrelate ? "" : " disabled"}>All triggers within a time window</option>
          </select>
          <small>${canCorrelate ? "Use correlation when several separate signals together make an event meaningful. Trigger order does not matter." : "Add a second trigger to enable correlated matching."}</small>
        </label>
        ${correlated ? `<label>Correlation window (seconds)
          <input type="number" min="1" max="86400" data-path="match_window" value="${definition.match_window || 60}">
          <small>Every configured trigger must occur within this window. Partial matches reset after the window or a Home Assistant restart.</small>
        </label>` : ""}
      </section>`,
    );
  }

  if (!this.shadowRoot.querySelector("#companion-options")) {
    const options = definition.delivery?.companion || {};
    const actions = options.actions || [];
    anchor.insertAdjacentHTML(
      "beforebegin",
      `<section class="subcard" id="companion-options">
        <div class="subhead"><strong>Companion App options</strong></div>
        <small>Optional extras for Home Assistant mobile-app notify targets. These do not expose arbitrary notification data.</small>
        <label>Open when notification is tapped
          <input id="companion-url" type="text" value="${escLocal(options.url || "")}" placeholder="/lovelace/security or https://example.com">
          <small>Use a Home Assistant path beginning with /, or an http/https URL.</small>
        </label>
        <div class="notify-targets"><strong>Action buttons</strong><small>Add up to three buttons.</small>
          ${actions.map((item, index) => {
            const link = Boolean(item.uri);
            return `<div class="subcard companion-action" data-companion-action="${index}">
              <label>Button title<input type="text" data-companion-title="${index}" value="${escLocal(item.title || "")}"></label>
              <label>Button type<select data-companion-mode="${index}">
                <option value="action"${link ? "" : " selected"}>Fire Home Assistant event</option>
                <option value="uri"${link ? " selected" : ""}>Open link</option>
              </select></label>
              ${link
                ? `<label>Link<input type="text" data-companion-uri="${index}" value="${escLocal(item.uri || "")}" placeholder="/lovelace/security"></label>`
                : `<label>Action ID<input type="text" data-companion-id="${index}" value="${escLocal(item.action || "")}" placeholder="ACK_ALERT"></label>`}
              <button class="secondary" type="button" data-remove-companion="${index}">Remove button</button>
            </div>`;
          }).join("")}
          <button class="secondary" type="button" id="add-companion-action"${actions.length >= 3 ? " disabled" : ""}>+ Add action button</button>
        </div>
        ${this.errors?.companion ? `<div class="error">${escLocal(this.errors.companion)}</div>` : ""}
      </section>`,
    );
  }
};

panel.bind = function() {
  originalBind.call(this);
  this.shadowRoot.querySelector("#trigger-match-mode")?.addEventListener("change", event => {
    const value = event.currentTarget.value;
    this.editor.definition.match = value;
    if (value === "all_within") this.editor.definition.match_window ||= 60;
    else delete this.editor.definition.match_window;
    this.markDirty();
    this.render();
  });

  this.shadowRoot.querySelector("#companion-url")?.addEventListener("input", event => {
    const value = event.currentTarget.value.trim();
    if (value) companion(this.editor.definition).url = value;
    else if (this.editor.definition.delivery?.companion) delete this.editor.definition.delivery.companion.url;
    cleanupCompanion(this.editor.definition);
    this.markDirty(); this.updatePreview();
  });
  this.shadowRoot.querySelector("#add-companion-action")?.addEventListener("click", () => {
    const options = companion(this.editor.definition);
    options.actions ??= [];
    if (options.actions.length < 3) options.actions.push({title:"Acknowledge", action:"ACK_ALERT"});
    this.markDirty(); this.render();
  });
  this.shadowRoot.querySelectorAll("[data-remove-companion]").forEach(button => button.addEventListener("click", event => {
    const index = Number(event.currentTarget.dataset.removeCompanion);
    this.editor.definition.delivery.companion.actions.splice(index, 1);
    cleanupCompanion(this.editor.definition);
    this.markDirty(); this.render();
  }));
  this.shadowRoot.querySelectorAll("[data-companion-title]").forEach(input => input.addEventListener("input", event => {
    companion(this.editor.definition).actions[Number(event.currentTarget.dataset.companionTitle)].title = event.currentTarget.value;
    this.markDirty(); this.updatePreview();
  }));
  this.shadowRoot.querySelectorAll("[data-companion-mode]").forEach(select => select.addEventListener("change", event => {
    const index = Number(event.currentTarget.dataset.companionMode);
    const item = companion(this.editor.definition).actions[index];
    if (event.currentTarget.value === "uri") {
      delete item.action; item.uri = "/";
    } else {
      delete item.uri; item.action = "ACK_ALERT";
    }
    this.markDirty(); this.render();
  }));
  this.shadowRoot.querySelectorAll("[data-companion-uri]").forEach(input => input.addEventListener("input", event => {
    companion(this.editor.definition).actions[Number(event.currentTarget.dataset.companionUri)].uri = event.currentTarget.value.trim();
    this.markDirty(); this.updatePreview();
  }));
  this.shadowRoot.querySelectorAll("[data-companion-id]").forEach(input => input.addEventListener("input", event => {
    companion(this.editor.definition).actions[Number(event.currentTarget.dataset.companionId)].action = event.currentTarget.value.trim();
    this.markDirty(); this.updatePreview();
  }));
};

export { ConditionalNotificationsPanel };
