import { ConditionalNotificationsPanel } from "./conditional-notifications-panel-status.js";

const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;
const originalBind = panel.bind;
const originalPreview = panel.preview;
const originalValidate = panel.validate;
const originalStyles = panel.styles;
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

panel.styles = function() {
  return originalStyles.call(this).replace("</style>", `
    .dialog{width:min(900px,100%)}
    .dialog header p{max-width:620px}
    .editor-body>section{padding:26px 0}
    .editor-body h3{font-size:18px;margin-bottom:7px}
    .section-help{margin:0 0 16px;color:var(--secondary-text-color);font-size:14px;line-height:1.45}
    label{font-size:14px}
    .editor-summary{margin:20px 0 0;padding:16px 18px!important;border-radius:14px;background:color-mix(in srgb,var(--primary-color) 7%,var(--card-background-color));border:1px solid color-mix(in srgb,var(--primary-color) 18%,var(--divider-color))!important}
    .editor-summary h3{font-size:15px;margin:0 0 8px}
    .editor-summary div{font-size:13px;margin:5px 0;color:var(--secondary-text-color)}
    .editor-summary strong{color:var(--primary-text-color)}
    .subcard{background:color-mix(in srgb,var(--secondary-background-color) 48%,var(--card-background-color));border-color:color-mix(in srgb,var(--divider-color) 70%,transparent)}
    .compact-empty{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 14px;border:1px dashed var(--divider-color);border-radius:12px;margin:10px 0 12px}
    .compact-empty p{margin:0;color:var(--secondary-text-color);font-size:14px}
    .delivery-card{padding:15px 16px;border:1px solid var(--divider-color);border-radius:14px;background:var(--secondary-background-color);margin-top:14px}
    .delivery-card>.check{margin-top:0}
    .delivery-help{margin:2px 0 12px 32px}
    .delivery-group{padding:12px 0;border-top:1px solid var(--divider-color)}
    .delivery-group:first-of-type{border-top:0}
    .delivery-group strong{display:block;margin-bottom:3px}
    .delivery-group>small{display:block;color:var(--secondary-text-color);margin-bottom:9px;line-height:1.4}
    .more-options{padding:20px 0;border-bottom:1px solid var(--divider-color)}
    .more-options>summary{font-size:16px;padding:4px 0}
    .advanced-group{padding:18px 0;border-top:1px solid var(--divider-color)}
    .advanced-group:first-child{border-top:0}
    .advanced-group h4{margin:0 0 4px;font-size:15px}
    .advanced-group>.section-help{margin-bottom:10px}
    .correlation-card,.companion-card{margin:10px 0 0}
    @media(max-width:700px){.editor-summary{margin-top:14px}.compact-empty{align-items:flex-start;flex-direction:column}.compact-empty .secondary{width:100%}}
  </style>`);
};

panel.preview = function(definition) {
  const preview = originalPreview.call(this, definition);
  if (definition.match === "all_within") {
    const triggers = definition.triggers.map(trigger => this.triggerSummary(trigger)).join("; and ");
    preview.watching = `All configured triggers within ${this.duration(definition.match_window || 0)}: ${triggers}`;
  }
  if (definition.delivery?.use_defaults === false) {
    const parts = [];
    if (definition.delivery.persistent_notification) parts.push("persistent notification");
    const notifyCount = (definition.delivery.notify_entities || []).filter(Boolean).length;
    const satelliteCount = (definition.delivery.assist_satellites || []).filter(Boolean).length;
    const legacyCount = (definition.delivery.notify_services || []).filter(Boolean).length;
    if (notifyCount) parts.push(`${notifyCount} notify target${notifyCount === 1 ? "" : "s"}`);
    if (satelliteCount) parts.push(`${satelliteCount} voice satellite${satelliteCount === 1 ? "" : "s"}`);
    if (legacyCount) parts.push(`${legacyCount} legacy service${legacyCount === 1 ? "" : "s"}`);
    preview.delivery = parts.length ? parts.join(", ") : "No delivery target selected";
  }
  const options = definition.delivery?.companion;
  if (options?.url || options?.actions?.length) {
    const parts = [];
    if (options.url) parts.push(`tap opens ${options.url}`);
    if (options.actions?.length) parts.push(`${options.actions.length} action button${options.actions.length === 1 ? "" : "s"}`);
    preview.delivery += `; Companion App extras: ${parts.join(", ")}`;
  }
  return preview;
};

panel.validate = function(definition) {
  const errors = originalValidate.call(this, definition);
  const satellites = definition.delivery?.assist_satellites || [];
  if (definition.delivery?.use_defaults === false) {
    if (satellites.some(entityId => !entityId)) {
      errors.delivery = "Choose or remove each Assist satellite.";
    } else if (
      satellites.filter(Boolean).length
      && !(definition.delivery.notify_entities || []).some(entityId => !entityId)
    ) {
      delete errors.delivery;
    }
  }
  const options = definition.delivery?.companion;
  if (!options) return errors;
  const safeUri = (value) => (value?.startsWith("/") && !value.startsWith("//")) || /^https?:\/\/[^\s]+$/i.test(value || "");
  if (options.url && !safeUri(options.url)) errors.companion = "Use a Home Assistant path beginning with / or an http/https URL.";
  if ((options.actions || []).length > 3) errors.companion = "Use at most three Companion App buttons.";
  for (const item of options.actions || []) {
    if (!item.title?.trim()) errors.companion = "Give each Companion App button a title.";
    if (item.uri && !safeUri(item.uri)) errors.companion = "Button links must be Home Assistant paths or http/https URLs.";
    if (item.action && !/^[A-Za-z0-9_:-]{1,64}$/.test(item.action)) errors.companion = "Button action IDs may use letters, numbers, _, :, and -.";
    if (["URI", "REPLY"].includes(item.action)) errors.companion = "URI and REPLY are reserved Companion App action IDs.";
  }
  return errors;
};

panel.addAssistSatellite = function() {
  this.editor.definition.delivery ??= {use_defaults:false};
  this.editor.definition.delivery.assist_satellites ??= [];
  this.editor.definition.delivery.assist_satellites.push("");
  this.markDirty();
  this.render();
};

panel.removeAssistSatellite = function(index) {
  this.editor.definition.delivery.assist_satellites.splice(index, 1);
  this.markDirty();
  this.render();
};

panel.renderCustomDelivery = function(definition) {
  const delivery = definition.delivery ??= {use_defaults:false};
  const entities = delivery.notify_entities || [];
  const satellites = delivery.assist_satellites || [];
  return `<div class="delivery-card">
    <label class="check"><input type="checkbox" data-path="delivery.persistent_notification" ${delivery.persistent_notification?"checked":""}> Persistent notification in Home Assistant</label>
    <div class="delivery-group">
      <strong>Phones & notification devices</strong>
      <small>Targets provided by Home Assistant's notify integration.</small>
      ${entities.length?entities.map((entityId,index)=>`<div class="picker-row"><ha-entity-picker data-path="delivery.notify_entities.${index}" data-value="${escLocal(entityId)}" data-domain="notify"></ha-entity-picker><button class="icon danger" type="button" data-remove-notify-entity="${index}" aria-label="Remove notify target">×</button></div>`).join(""):`<p class="muted">No notify targets selected.</p>`}
      <button class="secondary" type="button" id="add-notify-entity">+ Add notify target</button>
    </div>
    <div class="delivery-group">
      <strong>Voice announcements</strong>
      <small>Speak the notification message on selected Assist satellites using <code>assist_satellite.announce</code>.</small>
      ${satellites.length?satellites.map((entityId,index)=>`<div class="picker-row"><ha-entity-picker data-path="delivery.assist_satellites.${index}" data-value="${escLocal(entityId)}" data-domain="assist_satellite"></ha-entity-picker><button class="icon danger" type="button" data-remove-assist-satellite="${index}" aria-label="Remove Assist satellite">×</button></div>`).join(""):`<p class="muted">No voice satellites selected.</p>`}
      <button class="secondary" type="button" id="add-assist-satellite">+ Add voice satellite</button>
    </div>
    ${(delivery.notify_services||[]).length?`<small class="legacy-note">Existing legacy notify services are retained: ${escLocal(delivery.notify_services.join(", "))}</small>`:""}
    ${this.errors?.delivery?`<div class="error">${escLocal(this.errors.delivery)}</div>`:""}
  </div>`;
};

panel.renderCorrelationOptions = function(definition) {
  const correlated = definition.match === "all_within";
  const canCorrelate = definition.triggers.length >= 2;
  return `<div class="subcard correlation-card">
    <div class="subhead"><strong>Multiple-trigger matching</strong></div>
    <label>Match mode
      <select id="trigger-match-mode">
        <option value="any"${correlated ? "" : " selected"}>Any trigger can notify</option>
        <option value="all_within"${correlated ? " selected" : ""}${canCorrelate ? "" : " disabled"}>All triggers within a time window</option>
      </select>
      <small>${canCorrelate ? "Use this when several separate signals together make an event meaningful. Trigger order does not matter." : "Add a second trigger to enable correlated matching."}</small>
    </label>
    ${correlated ? `<label>Correlation window (seconds)
      <input type="number" min="1" max="86400" data-path="match_window" value="${definition.match_window || 60}">
      <small>Every configured trigger must occur within this window.</small>
    </label>` : ""}
  </div>`;
};

panel.renderCompanionOptions = function(definition) {
  const options = definition.delivery?.companion || {};
  const actions = options.actions || [];
  return `<div class="subcard companion-card" id="companion-options">
    <div class="subhead"><strong>Companion App extras</strong></div>
    <small>Optional tap links and buttons for legacy <code>notify.mobile_app_*</code> service targets.</small>
    <label>Open when notification is tapped
      <input id="companion-url" type="text" value="${escLocal(options.url || "")}" placeholder="/lovelace/security or https://example.com">
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
  </div>`;
};

panel.renderEditor = function() {
  if (!this.editor) return "";
  const d = this.editor.definition;
  d.conditions ??= [];
  d.delivery ??= {use_defaults:true};
  const preview = this.preview(d);
  return `<div class="scrim" role="presentation"><div class="dialog" role="dialog" aria-modal="true" aria-labelledby="editor-title">
    <header><div><h2 id="editor-title">${this.editor.id?"Edit":"Create"} conditional notification</h2><p>Set the event, any checks, what to send, and how often. Less-common controls are under More options.</p></div><button class="icon" id="close-editor" aria-label="Close">×</button></header>
    <div class="editor-body">
      <section class="preview editor-summary">${this.previewMarkup(preview)}</section>

      <section><h3>Basics</h3><p class="section-help">Give this notification a name you will recognise later.</p><label>Name<input data-path="name" value="${escLocal(d.name)}" aria-invalid="${!!this.errors?.name}"></label>${this.errors?.name?`<div class="error">${escLocal(this.errors.name)}</div>`:""}<label>Description (optional)<input data-path="description" value="${escLocal(d.description||"")}"></label></section>

      <section><h3>When</h3><p class="section-help">A trigger starts the check. Add more only when several different events should be able to notify you.</p>${d.triggers.map((t,i)=>this.renderTrigger(t,i)).join("")}<button class="secondary" type="button" id="add-trigger">+ Add another trigger</button></section>

      <section><h3>Only if</h3><p class="section-help">Optional checks that must be true when the trigger happens.</p>${d.conditions.length?d.conditions.map((c,i)=>this.renderCondition(c,i)).join(""):`<div class="compact-empty"><p>No conditions — the trigger alone is enough.</p><button class="secondary" type="button" id="add-condition">+ Add condition</button></div>`}${d.conditions.length?`<button class="secondary" type="button" id="add-condition">+ Add condition</button>`:""}</section>

      <section><h3>Send</h3><p class="section-help">Write the notification, then choose where it should go.</p><label>Title<input data-path="title" value="${escLocal(d.title)}"></label><label>Message<textarea data-path="message" rows="3">${escLocal(d.message)}</textarea><small>Templates can use trigger.entity_id, friendly_name, values, event data, and timestamp.</small></label><label class="check"><input type="checkbox" data-path="delivery.use_defaults" ${d.delivery.use_defaults!==false?"checked":""}> Use my default delivery targets</label><div class="delivery-help"><small>${d.delivery.use_defaults!==false?"Using the persistent-notification, notify-device, and voice-satellite defaults from integration settings.":"This notification uses its own delivery targets."}</small><a href="/config/integrations/integration/conditional_notifications">Integration settings</a></div>${d.delivery.use_defaults===false?this.renderCustomDelivery(d):""}</section>

      <section><h3>Then</h3><p class="section-help">Choose whether this watch finishes after notifying or keeps listening.</p><div class="choice-row">${[["once","Once"],["every","Every trigger"],["limited","Limited count"]].map(([v,l])=>`<label class="choice"><input type="radio" name="repeat" data-path="repeat_policy" value="${v}" ${d.repeat_policy===v?"checked":""}><span><strong>${l}</strong><small>${v==="once"?"Notify once, then stop":v==="every"?"Keep watching after each match":"Stop after a chosen number"}</small></span></label>`).join("")}</div>${d.repeat_policy==="limited"?`<label>Maximum notifications<input type="number" min="1" data-path="max_notifications" value="${d.max_notifications||3}"></label>`:""}</section>

      <details class="more-options"><summary>More options</summary><div class="advanced">
        <div class="advanced-group"><h4>Schedule & expiry</h4><p class="section-help">Leave these blank for a notification that starts now and has no deadline.</p><div class="grid"><label>Available from<input type="datetime-local" data-path="available_from" value="${this.dateTimeValue(d.available_from)}"></label><label>Expires at<input type="datetime-local" data-path="expires_at" value="${this.dateTimeValue(d.expires_at)}"></label></div><label class="check"><input type="checkbox" data-path="notify_on_expiry" ${d.notify_on_expiry?"checked":""}> Notify me if nothing qualifies before expiry</label>${d.notify_on_expiry?`<div class="grid"><label>Expiry title<input data-path="expiry_title" value="${escLocal(d.expiry_title||`Expired: ${d.name}`)}"></label><label>Expiry message<input data-path="expiry_message" value="${escLocal(d.expiry_message||"No qualifying event occurred.")}"></label></div>`:""}</div>
        <div class="advanced-group"><h4>Timing protection</h4><div class="grid"><label>Cooldown (seconds)<input type="number" min="0" data-path="cooldown" value="${d.cooldown||""}"><small>Minimum time after a notification before another is allowed.</small></label><label>Debounce (seconds)<input type="number" min="0" data-path="debounce" value="${d.debounce||""}"><small>Ignore rapid repeated changes within this period.</small></label></div><label class="check"><input type="checkbox" data-path="match_current_state" ${d.match_current_state?"checked":""}> Match the current state immediately when first created</label></div>
        <div class="advanced-group"><h4>Recurring hours</h4><label class="check"><input type="checkbox" id="recurring-toggle" ${d.active_window?"checked":""}> Limit to a recurring local-time window</label>${d.active_window?`<div class="grid"><label>Window starts<input type="time" data-path="active_window.start" value="${escLocal(d.active_window.start)}"></label><label>Window ends<input type="time" data-path="active_window.end" value="${escLocal(d.active_window.end)}"></label></div><label>Active weekdays<input id="weekdays" value="${escLocal(d.active_window.weekdays.join(", "))}"><small>Comma-separated weekdays. Overnight hours after midnight belong to the start day.</small></label>`:""}</div>
        <div class="advanced-group"><h4>Resolution</h4><label class="check"><input type="checkbox" id="resolve-toggle" ${d.resolve_when?"checked":""}> Auto-resolve when a state clears</label>${d.resolve_when?`<div class="grid"><label>Resolution entity<ha-entity-picker data-path="resolve_when.entity_id" data-value="${escLocal(d.resolve_when.entity_id||"")}"></ha-entity-picker></label><label>Resolution state<input data-path="resolve_when.to" value="${escLocal(d.resolve_when.to||"off")}"></label></div><label class="check"><input type="checkbox" data-path="clear_on_resolve" ${d.clear_on_resolve!==false?"checked":""}> Clear the tagged persistent notification when resolved</label>`:""}</div>
        <div class="advanced-group"><h4>Multiple triggers</h4>${this.renderCorrelationOptions(d)}</div>
        <div class="advanced-group"><h4>Companion App</h4>${this.renderCompanionOptions(d)}</div>
      </div></details>
    </div><footer><button class="secondary" id="cancel-editor">Cancel</button><button class="primary" id="save-editor">${this.editor.id?"Save changes":"Create notification"}</button></footer>
  </div></div>`;
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
};

panel.bind = function() {
  originalBind.call(this);
  if (!this.editor) return;

  this.shadowRoot.querySelector("#add-assist-satellite")?.addEventListener("click", () => this.addAssistSatellite());
  this.shadowRoot.querySelectorAll("[data-remove-assist-satellite]").forEach(button => button.addEventListener("click", event => {
    this.removeAssistSatellite(Number(event.currentTarget.dataset.removeAssistSatellite));
  }));

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
