const WS = "conditional_notifications";

const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const fmt = (value) => {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Invalid date";
  try {
    return new Intl.DateTimeFormat(undefined, {dateStyle:"medium", timeStyle:"short"}).format(date);
  } catch (_error) {
    return "Invalid date";
  }
};
const entityName = (id) => (id || "an entity").split(".").pop().replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());

class ConditionalNotificationsPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this.records = [];
    this.history = [];
    this.tab = "active";
    this.search = "";
    this.editor = null;
    this.editorScrollTop = 0;
    this.advancedOpen = false;
    this.dirty = false;
    this.loading = true;
    this.toast = "";
  }

  set hass(value) {
    this._hass = value;
    if (!this.loaded) this.load();
    this.bindHass();
  }
  get hass() { return this._hass; }
  set panel(value) { this._panel = value; }
  set narrow(value) { if (this._narrow === value) return; this._narrow = value; this.render(); }
  set route(value) { this._route = value; }

  connectedCallback() {
    this.render();
    this.beforeUnload = (event) => { if (this.dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", this.beforeUnload);
  }
  disconnectedCallback() {
    window.removeEventListener("beforeunload", this.beforeUnload);
    if (this.unsubscribe) this.unsubscribe();
  }

  async load() {
    if (!this.hass) return;
    this.loaded = true;
    try {
      [this.records, this.history] = await Promise.all([
        this.hass.callWS({type:`${WS}/list`}),
        this.hass.callWS({type:`${WS}/history`}),
      ]);
      this.unsubscribe = await this.hass.connection.subscribeMessage(() => this.refresh(), {type:`${WS}/subscribe`});
    } catch (error) { this.showToast(error.message || String(error)); }
    this.loading = false;
    this.render();
  }
  async refresh() {
    if (!this.hass) return;
    [this.records, this.history] = await Promise.all([
      this.hass.callWS({type:`${WS}/list`, query:this.search || undefined}),
      this.hass.callWS({type:`${WS}/history`}),
    ]);
    // Replacing the editor DOM while somebody is typing destroys the focused
    // control. The refreshed records will be rendered when the editor closes.
    if (!this.editor) this.render();
  }
  async action(id, action) {
    if (action === "delete" && !confirm("Delete this conditional notification? This cannot be undone.")) return;
    this.pendingActions ??= new Set();
    const pendingKey = `${id}:${action}`;
    if (this.pendingActions.has(pendingKey)) return;
    this.pendingActions.add(pendingKey);
    try {
      const result = await this.hass.callWS({type:`${WS}/action`, notification_id:id, action});
      this.showToast(action === "test" ? "Test notification sent" : action === "duplicate" ? "Notification duplicated" : `${action[0].toUpperCase()}${action.slice(1)} complete`);
      if (result?.deleted && this.editor?.id === id) this.closeEditor(true);
      await this.refresh();
    } catch (error) {
      this.showToast(error.message || String(error));
    } finally {
      this.pendingActions.delete(pendingKey);
    }
  }

  newDefinition(record) {
    const d = record?.definition;
    return d ? structuredClone(d) : {
      name:"", triggers:[{type:"state", entity_id:"", to:"on"}], match:"any",
      conditions:[], title:"{{ trigger.friendly_name }}", message:"{{ trigger.friendly_name }} matched at {{ now().strftime('%H:%M') }}.",
      repeat_policy:"once", delivery:{use_defaults:true}, notify_on_expiry:false,
    };
  }
  openEditor(record) {
    this.editor = {id:record?.id, definition:this.newDefinition(record), original:record};
    this.editorScrollTop = 0;
    this.advancedOpen = false;
    this.dirty = false;
    this.render();
    requestAnimationFrame(() => this.shadowRoot.querySelector(".dialog input")?.focus());
  }
  closeEditor(force=false) {
    if (!force && this._conditionalNotificationsSavePromise) {
      this.showToast("Save in progress");
      return;
    }
    if (!force && this.dirty && !confirm("Discard your unsaved changes?")) return;
    this.editor = null; this.dirty = false; this.render();
  }
  markDirty() { this.dirty = true; }

  triggerSummary(trigger) {
    const name = entityName(trigger.entity_id || trigger.trigger_id || trigger.event_type);
    if (trigger.type === "state") return `${name} changes${trigger.to !== undefined ? ` to ${trigger.to}` : ""}${trigger.for ? ` and stays there for ${this.duration(trigger.for)}` : ""}`;
    if (trigger.type === "numeric_state") return `${name} enters ${trigger.above !== undefined ? `above ${trigger.above}` : ""}${trigger.above !== undefined && trigger.below !== undefined ? " and " : ""}${trigger.below !== undefined ? `below ${trigger.below}` : ""}`;
    if (trigger.type === "zone") return `${name} ${trigger.event}s ${entityName(trigger.zone_entity_id)}`;
    if (trigger.type === "event") return `event ${trigger.event_type || "(choose an event)"} fires`;
    return `named event ${trigger.trigger_id || "(choose a name)"} fires`;
  }
  conditionSummary(condition) {
    const name = entityName(condition.entity_id);
    if (condition.type === "state") return `${name} ${condition.negate ? "is not" : "is"} ${condition.state || "(choose a state)"}${condition.attribute ? ` for attribute ${condition.attribute}` : ""}`;
    if (condition.type === "numeric_state") return `${name} is ${condition.above !== undefined ? `above ${condition.above}` : ""}${condition.above !== undefined && condition.below !== undefined ? " and " : ""}${condition.below !== undefined ? `below ${condition.below}` : ""}`;
    if (condition.type === "zone") return `${name} is in ${entityName(condition.zone_entity_id)}`;
    const times = `${condition.after ? `after ${condition.after}` : ""}${condition.after && condition.before ? " and " : ""}${condition.before ? `before ${condition.before}` : ""}`;
    return `${times || "during the selected time"}${condition.weekdays?.length ? ` on ${condition.weekdays.join(", ")}` : ""}`;
  }
  duration(seconds) {
    const n = Number(seconds || 0); if (!n) return "0 seconds";
    if (n % 3600 === 0) return `${n/3600} hour${n===3600?"":"s"}`;
    if (n % 60 === 0) return `${n/60} minute${n===60?"":"s"}`;
    return `${n} seconds`;
  }
  dateTimeValue(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0,16);
  }
  preview(d) {
    const watching = d.triggers.map(t => this.triggerSummary(t)).join(" or ");
    const active = `${d.available_from ? fmt(d.available_from) : "Now"}${d.expires_at ? ` until ${fmt(d.expires_at)}` : ", with no expiry"}`;
    const conditions = d.conditions?.length ? d.conditions.map(c=>this.conditionSummary(c)).join("; and ") : "No extra conditions";
    let behaviour = d.repeat_policy === "once" ? "Notify on the first qualifying match, then stop" : d.repeat_policy === "limited" ? `Notify on the next ${d.max_notifications || "?"} matches` : "Notify on every distinct match";
    if (d.cooldown) behaviour += `, at most once every ${this.duration(d.cooldown)}`;
    return {watching, active, conditions, behaviour, delivery:d.delivery?.use_defaults !== false ? "Use my notification defaults" : "Custom delivery", expiry:d.notify_on_expiry ? "Notify me if nothing qualifies" : "Expire silently", resolution:d.resolve_when?`Resolve when ${this.triggerSummary(d.resolve_when)}`:"No automatic resolution"};
  }

  previewMarkup(preview) {
    return `<h3>Plain-English preview</h3>${Object.entries(preview).map(([k,v])=>`<div><strong>${k[0].toUpperCase()+k.slice(1)}:</strong> ${esc(v)}</div>`).join("")}`;
  }

  updatePreview() {
    const preview = this.shadowRoot?.querySelector(".preview");
    if (preview && this.editor) preview.innerHTML = this.previewMarkup(this.preview(this.editor.definition));
  }

  onField(event) {
    const target = event.currentTarget;
    const path = target.dataset.path?.split(".");
    if (!path) return;
    let object = this.editor.definition;
    for (const part of path.slice(0,-1)) object = object[part];
    let value = event.detail?.value ?? (target.type === "checkbox" ? target.checked : target.value);
    if (target.type === "number") value = value === "" ? undefined : Number(value);
    if (target.type === "datetime-local") value = value ? new Date(value).toISOString() : undefined;
    const field = path.at(-1);
    if (Array.isArray(object)) object[Number(field)] = value ?? "";
    else if (value === undefined || value === "") delete object[field];
    else object[field] = value;
    if (target.localName === "ha-entity-picker") target.dataset.value = value ?? "";
    this.markDirty();
    const structuralFields = new Set(["repeat_policy", "notify_on_expiry", "delivery.use_defaults"]);
    if (event.type === "change" && structuralFields.has(path.join("."))) this.render();
    else this.updatePreview();
  }
  addTrigger() { this.editor.definition.triggers.push({type:"state", entity_id:"", to:"on"}); this.markDirty(); this.render(); }
  removeTrigger(index) { if (this.editor.definition.triggers.length > 1) { this.editor.definition.triggers.splice(index,1); this.markDirty(); this.render(); } }
  changeTrigger(index, type) {
    const defaults = {state:{type,entity_id:"",to:"on"},numeric_state:{type,entity_id:"",above:0},zone:{type,entity_id:"",zone_entity_id:"zone.home",event:"enter"},event:{type,event_type:""},named:{type,trigger_id:""}};
    this.editor.definition.triggers[index] = defaults[type]; this.markDirty(); this.render();
  }
  addCondition() { this.editor.definition.conditions.push({type:"state",entity_id:"",state:"not_home"}); this.markDirty(); this.render(); }
  removeCondition(index) { this.editor.definition.conditions.splice(index,1); this.markDirty(); this.render(); }
  changeCondition(index, type) {
    const defaults = {state:{type,entity_id:"",state:"on"},numeric_state:{type,entity_id:"",above:0},zone:{type,entity_id:"",zone_entity_id:"zone.home"},time:{type,after:"09:00",before:"17:00",weekdays:[]}};
    this.editor.definition.conditions[index] = defaults[type]; this.markDirty(); this.render();
  }
  toggleConditionWeekday(index, weekday, checked) {
    const condition = this.editor.definition.conditions[index];
    condition.weekdays ??= [];
    condition.weekdays = checked ? [...new Set([...condition.weekdays, weekday])] : condition.weekdays.filter(day=>day!==weekday);
    if (!condition.weekdays.length) delete condition.weekdays;
    this.markDirty(); this.updatePreview();
  }
  toggleRecurring(enabled) { if (enabled) this.editor.definition.active_window={start:"22:00",end:"07:00",weekdays:["monday","tuesday","wednesday","thursday","friday"]}; else delete this.editor.definition.active_window; this.markDirty(); this.render(); }
  toggleResolve(enabled) { if (enabled) this.editor.definition.resolve_when={type:"state",entity_id:this.editor.definition.triggers[0]?.entity_id||"",to:"off"}; else delete this.editor.definition.resolve_when; this.markDirty(); this.render(); }
  addNotifyEntity() { this.editor.definition.delivery.notify_entities ??= []; this.editor.definition.delivery.notify_entities.push(""); this.markDirty(); this.render(); }
  removeNotifyEntity(index) { this.editor.definition.delivery.notify_entities.splice(index,1); this.markDirty(); this.render(); }
  setAdvancedOpen(open) { this.advancedOpen = open; }
  setWeekdays(value) { this.editor.definition.active_window.weekdays=value.split(",").map(x=>x.trim().toLowerCase()).filter(Boolean); this.markDirty(); }

  validate(d) {
    const errors = {};
    if (!d.name?.trim()) errors.name = "Give this notification a name.";
    if (!d.triggers?.length) errors.triggers = "Add at least one trigger.";
    d.triggers?.forEach((t,i) => {
      if (["state","numeric_state","zone"].includes(t.type) && !t.entity_id) errors[`trigger${i}`] = "Choose an entity.";
      if (t.type === "numeric_state" && t.above === undefined && t.below === undefined) errors[`trigger${i}`] = "Set an above or below threshold.";
      if (t.type === "event" && !t.event_type) errors[`trigger${i}`] = "Enter an event type.";
    });
    d.conditions?.forEach((c,i) => {
      if (["state","numeric_state","zone"].includes(c.type) && !c.entity_id) errors[`condition${i}`] = "Choose an entity.";
      if (c.type === "state" && c.state === undefined) errors[`condition${i}`] = "Enter the required state.";
      if (c.type === "numeric_state" && c.above === undefined && c.below === undefined) errors[`condition${i}`] = "Set an above or below threshold.";
      if (c.type === "zone" && !c.zone_entity_id) errors[`condition${i}`] = "Choose a zone.";
      if (c.type === "time" && !c.after && !c.before) errors[`condition${i}`] = "Set an after or before time.";
    });
    if (!d.title?.trim()) errors.title = "Enter a title.";
    if (!d.message?.trim()) errors.message = "Enter a message.";
    if (d.repeat_policy === "limited" && (!d.max_notifications || d.max_notifications < 1)) errors.repeat = "Enter a positive count.";
    if (d.expires_at && d.available_from && new Date(d.expires_at) <= new Date(d.available_from)) errors.expires_at = "Expiry must be after availability.";
    if (d.delivery?.use_defaults === false) {
      const entities = d.delivery.notify_entities || [];
      if (entities.some(entityId => !entityId)) errors.delivery = "Choose or remove each notify entity.";
      if (!d.delivery.persistent_notification && !entities.filter(Boolean).length && !(d.delivery.notify_services || []).length) errors.delivery = "Choose at least one delivery channel.";
    }
    return errors;
  }
  async save() {
    const d = this.editor.definition;
    this.errors = this.validate(d);
    if (Object.keys(this.errors).length) { this.showToast("Check the highlighted fields"); this.render(); return; }
    try {
      const editing = Boolean(this.editor.id);
      if (editing) await this.hass.callWS({type:`${WS}/update`,notification_id:this.editor.id,changes:d});
      else await this.hass.callWS({type:`${WS}/create`,definition:d});
      this.dirty = false; this.closeEditor(true); this.showToast(editing ? "Changes saved" : "Notification created"); await this.refresh();
    } catch (error) { this.showToast(error.message || String(error)); }
  }

  renderTrigger(trigger, index) {
    const p = `triggers.${index}`;
    return `<section class="subcard">
      <div class="subhead"><strong>Trigger ${index+1}</strong>${this.editor.definition.triggers.length>1?`<button class="icon danger" data-remove-trigger="${index}" aria-label="Remove trigger">×</button>`:""}</div>
      <label>Trigger type<select data-trigger-type="${index}">${["state","numeric_state","zone","event","named"].map(x=>`<option value="${x}" ${trigger.type===x?"selected":""}>${x.replace("_"," ")}</option>`).join("")}</select></label>
      ${["state","numeric_state","zone"].includes(trigger.type)?`<label>Entity<ha-entity-picker data-path="${p}.entity_id" data-value="${esc(trigger.entity_id||"")}"></ha-entity-picker></label>`:""}
      ${trigger.type==="state"?`<div class="grid"><label>From (optional)<input data-path="${p}.from" value="${esc(trigger.from||"")}"></label><label>To<input data-path="${p}.to" value="${esc(trigger.to||"")}"></label></div><label>Minimum duration (seconds)<input type="number" min="0" data-path="${p}.for" value="${trigger.for||""}"><small>Require the state to persist before it qualifies.</small></label>`:""}
      ${trigger.type==="numeric_state"?`<div class="grid"><label>Above<input type="number" data-path="${p}.above" value="${trigger.above??""}"></label><label>Below<input type="number" data-path="${p}.below" value="${trigger.below??""}"></label></div>`:""}
      ${trigger.type==="zone"?`<label>Zone<ha-entity-picker data-path="${p}.zone_entity_id" data-value="${esc(trigger.zone_entity_id||"zone.home")}" data-domain="zone"></ha-entity-picker></label><label>Event<select data-path="${p}.event"><option value="enter" ${trigger.event==="enter"?"selected":""}>Enter</option><option value="leave" ${trigger.event==="leave"?"selected":""}>Leave</option></select></label>`:""}
      ${trigger.type==="event"?`<label>Event type<input data-path="${p}.event_type" value="${esc(trigger.event_type||"")}"></label>`:""}
      ${trigger.type==="named"?`<label>Semantic trigger name<input data-path="${p}.trigger_id" value="${esc(trigger.trigger_id||"")}"></label>`:""}
      ${this.errors?.[`trigger${index}`]?`<div class="error">${esc(this.errors[`trigger${index}`])}</div>`:""}
    </section>`;
  }

  renderCondition(condition, index) {
    const p = `conditions.${index}`;
    const weekdays = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"];
    return `<div class="subcard"><div class="subhead"><strong>Condition ${index+1}</strong><button class="icon danger" data-remove-condition="${index}" aria-label="Remove condition">×</button></div>
      <label>Condition type<select data-condition-type="${index}">${["state","numeric_state","zone","time"].map(type=>`<option value="${type}" ${condition.type===type?"selected":""}>${type.replace("_"," ")}</option>`).join("")}</select></label>
      ${["state","numeric_state","zone"].includes(condition.type)?`<label>Entity<ha-entity-picker data-path="${p}.entity_id" data-value="${esc(condition.entity_id||"")}"></ha-entity-picker></label>`:""}
      ${condition.type==="state"?`<div class="grid"><label>Required state<input data-path="${p}.state" value="${esc(condition.state??"")}"></label><label>Attribute (optional)<input data-path="${p}.attribute" value="${esc(condition.attribute||"")}"></label></div><label class="check"><input type="checkbox" data-path="${p}.negate" ${condition.negate?"checked":""}> State must not equal this value</label>`:""}
      ${condition.type==="numeric_state"?`<div class="grid"><label>Above<input type="number" data-path="${p}.above" value="${condition.above??""}"></label><label>Below<input type="number" data-path="${p}.below" value="${condition.below??""}"></label></div><label>Attribute (optional)<input data-path="${p}.attribute" value="${esc(condition.attribute||"")}"></label>`:""}
      ${condition.type==="zone"?`<label>Zone<ha-entity-picker data-path="${p}.zone_entity_id" data-value="${esc(condition.zone_entity_id||"zone.home")}" data-domain="zone"></ha-entity-picker></label>`:""}
      ${condition.type==="time"?`<div class="grid"><label>After (optional)<input type="time" data-path="${p}.after" value="${esc(condition.after||"")}"></label><label>Before (optional)<input type="time" data-path="${p}.before" value="${esc(condition.before||"")}"></label></div><fieldset class="weekdays"><legend>Weekdays (all if none selected)</legend>${weekdays.map(day=>`<label class="check"><input type="checkbox" data-condition-weekday="${index}" value="${day}" ${condition.weekdays?.includes(day)?"checked":""}> ${day[0].toUpperCase()+day.slice(1,3)}</label>`).join("")}</fieldset>`:""}
      ${this.errors?.[`condition${index}`]?`<div class="error">${esc(this.errors[`condition${index}`])}</div>`:""}
    </div>`;
  }

  renderCustomDelivery(d) {
    const entities = d.delivery.notify_entities || [];
    return `<div class="notify-targets"><strong>Notify entities</strong><small>Select phones, speakers, or other notify entities.</small>
      ${entities.length?entities.map((entityId,index)=>`<div class="picker-row"><ha-entity-picker data-path="delivery.notify_entities.${index}" data-value="${esc(entityId)}" data-domain="notify"></ha-entity-picker><button class="icon danger" data-remove-notify-entity="${index}" aria-label="Remove notify entity">×</button></div>`).join(""):`<p class="muted">No notify entities selected.</p>`}
      <button class="secondary" id="add-notify-entity">+ Add notify entity</button></div>
      ${(d.delivery.notify_services||[]).length?`<small class="legacy-note">Existing legacy services are still retained: ${esc(d.delivery.notify_services.join(", "))}</small>`:""}
      ${this.errors?.delivery?`<div class="error">${esc(this.errors.delivery)}</div>`:""}`;
  }

  hydrateEditor() {
    if (!this.editor) return;
    const defaults = this.shadowRoot.querySelector('[data-path="delivery.use_defaults"]')?.closest("label");
    defaults?.insertAdjacentHTML("afterend", `<div class="delivery-help"><small>Defaults are the integration-wide persistent-notification and notify-entity choices.</small><a href="/config/integrations/integration/conditional_notifications">Open integration settings</a></div>`);
    const legacyField = this.shadowRoot.querySelector("#notify-services")?.closest("label");
    if (legacyField) legacyField.outerHTML = this.renderCustomDelivery(this.editor.definition);
  }

  captureEditorState() {
    const editorBody = this.shadowRoot?.querySelector(".editor-body");
    if (editorBody) this.editorScrollTop = editorBody.scrollTop;
  }

  restoreEditorState() {
    const details = this.shadowRoot?.querySelector(".dialog details");
    if (details) details.open = this.advancedOpen;
    const editorBody = this.shadowRoot?.querySelector(".editor-body");
    if (editorBody) editorBody.scrollTop = this.editorScrollTop;
  }

  renderEditor() {
    if (!this.editor) return "";
    const d = this.editor.definition, preview = this.preview(d);
    return `<div class="scrim" role="presentation"><div class="dialog" role="dialog" aria-modal="true" aria-labelledby="editor-title">
      <header><div><h2 id="editor-title">${this.editor.id?"Edit":"Create"} conditional notification</h2><p>Choose what should happen. Advanced controls stay out of the way until you need them.</p></div><button class="icon" id="close-editor" aria-label="Close">×</button></header>
      <div class="editor-body">
        <section><h3>Basics</h3><label>Name<input data-path="name" value="${esc(d.name)}" aria-invalid="${!!this.errors?.name}"></label>${this.errors?.name?`<div class="error">${this.errors.name}</div>`:""}<label>Description (optional)<input data-path="description" value="${esc(d.description||"")}"></label></section>
        <section><h3>Notify when any of these happen</h3>${d.triggers.map((t,i)=>this.renderTrigger(t,i)).join("")}<button class="secondary" id="add-trigger">+ Add another trigger</button></section>
        <section><h3>Only notify if</h3>${d.conditions.length?d.conditions.map((c,i)=>this.renderCondition(c,i)).join(""):`<p class="muted">No extra conditions. A trigger alone is enough.</p>`}<button class="secondary" id="add-condition">+ Add condition</button></section>
        <section><h3>Notification</h3><label>Title<input data-path="title" value="${esc(d.title)}"></label><label>Message<textarea data-path="message" rows="3">${esc(d.message)}</textarea><small>Templates can use trigger.entity_id, friendly_name, values, event data, and timestamp.</small></label></section>
        <section><h3>Behaviour</h3><div class="choice-row">${[["once","Once"],["every","Every trigger"],["limited","Limited count"]].map(([v,l])=>`<label class="choice"><input type="radio" name="repeat" data-path="repeat_policy" value="${v}" ${d.repeat_policy===v?"checked":""}><span><strong>${l}</strong><small>${v==="once"?"Notify once, then stop":v==="every"?"Keep watching after each match":"Stop after a chosen number"}</small></span></label>`).join("")}</div>${d.repeat_policy==="limited"?`<label>Maximum notifications<input type="number" min="1" data-path="max_notifications" value="${d.max_notifications||3}"></label>`:""}</section>
        <section><h3>Active period</h3><div class="grid"><label>Available from<input type="datetime-local" data-path="available_from" value="${this.dateTimeValue(d.available_from)}"><small>Your browser provides the local date and time picker.</small></label><label>Expires at<input type="datetime-local" data-path="expires_at" value="${this.dateTimeValue(d.expires_at)}"><small>Expiry is an absolute deadline and continues while paused or disabled.</small></label></div><label class="check"><input type="checkbox" data-path="notify_on_expiry" ${d.notify_on_expiry?"checked":""}> Notify me if nothing qualifies before expiry</label>${d.notify_on_expiry?`<div class="grid"><label>Expiry title<input data-path="expiry_title" value="${esc(d.expiry_title||`Expired: ${d.name}`)}"></label><label>Expiry message<input data-path="expiry_message" value="${esc(d.expiry_message||"No qualifying event occurred.")}"></label></div>`:""}</section>
        <details><summary>Advanced options</summary><div class="advanced"><div class="grid"><label>Cooldown (seconds)<input type="number" min="0" data-path="cooldown" value="${d.cooldown||""}"><small>Minimum time after a notification before another is allowed.</small></label><label>Debounce (seconds)<input type="number" min="0" data-path="debounce" value="${d.debounce||""}"><small>Ignore rapid repeated changes within this period.</small></label></div><label class="check"><input type="checkbox" data-path="match_current_state" ${d.match_current_state?"checked":""}> Match the current state immediately when first created</label><label class="check"><input type="checkbox" id="recurring-toggle" ${d.active_window?"checked":""}> Limit to a recurring local-time window</label>${d.active_window?`<div class="grid"><label>Window starts<input type="time" data-path="active_window.start" value="${esc(d.active_window.start)}"></label><label>Window ends<input type="time" data-path="active_window.end" value="${esc(d.active_window.end)}"></label></div><label>Active weekdays<input id="weekdays" value="${esc(d.active_window.weekdays.join(", "))}"><small>Comma-separated weekdays. Overnight hours after midnight belong to the start day.</small></label>`:""}<label class="check"><input type="checkbox" id="resolve-toggle" ${d.resolve_when?"checked":""}> Auto-resolve when a state clears</label>${d.resolve_when?`<div class="grid"><label>Resolution entity<ha-entity-picker data-path="resolve_when.entity_id" data-value="${esc(d.resolve_when.entity_id||"")}"></ha-entity-picker></label><label>Resolution state<input data-path="resolve_when.to" value="${esc(d.resolve_when.to||"off")}"></label></div><label class="check"><input type="checkbox" data-path="clear_on_resolve" ${d.clear_on_resolve!==false?"checked":""}> Clear the tagged persistent notification when resolved</label>`:""}<label class="check"><input type="checkbox" data-path="delivery.use_defaults" ${d.delivery?.use_defaults!==false?"checked":""}> Use my notification defaults</label>${d.delivery?.use_defaults===false?`<label class="check"><input type="checkbox" data-path="delivery.persistent_notification" ${d.delivery.persistent_notification?"checked":""}> Persistent notification</label><label>Notify services<input id="notify-services" value="${esc((d.delivery.notify_services||[]).join(", "))}"><small>For example: notify.mobile_app_conors_phone</small></label>`:""}</div></details>
        <section class="preview">${this.previewMarkup(preview)}</section>
      </div><footer><button class="secondary" id="cancel-editor">Cancel</button><button class="primary" id="save-editor">${this.editor.id?"Save changes":"Create notification"}</button></footer>
    </div></div>`;
  }

  renderCard(record) {
    const trigger = record.definition.triggers.map(t=>this.triggerSummary(t)).join(" or ");
    const ready = record.next_eligible_at && new Date(record.next_eligible_at)>new Date() ? `Cooldown until ${fmt(record.next_eligible_at)}` : record.currently_active ? "Ready" : "Outside active period";
    return `<article class="card" tabindex="0" data-open="${record.id}"><div class="card-top"><div class="status-icon ${record.status}">●</div><div class="grow"><h3>${esc(record.name)}</h3><p>${esc(trigger)}</p></div><span class="badge">${esc(record.status)}</span></div><div class="meta"><span>🔔 ${record.notification_count} sent${record.remaining_notifications!==null?` · ${record.remaining_notifications} remaining`:""}</span><span>◷ ${esc(ready)}</span>${record.definition.expires_at?`<span>⌛ ${fmt(record.definition.expires_at)}</span>`:""}</div><div class="quick"><button data-action="${record.paused?"resume":"pause"}" data-id="${record.id}">${record.paused?"Resume":"Pause"}</button><button data-action="test" data-id="${record.id}">Test</button><button data-action="duplicate" data-id="${record.id}">Duplicate</button><button data-edit="${record.id}">Edit</button><button class="danger" data-action="delete" data-id="${record.id}">Delete</button></div></article>`;
  }
  filtered() {
    let items = this.records;
    if (this.search) { const q=this.search.toLowerCase(); items=items.filter(r=>JSON.stringify([r.name,r.description,r.definition.triggers]).toLowerCase().includes(q)); }
    if (this.tab==="active") return items.filter(r=>!r.paused && r.status!=="expired" && r.enabled);
    if (this.tab==="paused") return items.filter(r=>r.paused || r.status==="disabled");
    if (this.tab==="expired") return items.filter(r=>r.status==="expired");
    return items;
  }
  renderHistory() {
    const entries = this.history.filter(h=>!this.search || JSON.stringify(h).toLowerCase().includes(this.search.toLowerCase()));
    return entries.length?`<div class="timeline">${entries.slice(0,200).map(h=>`<div class="history-item"><span class="dot"></span><div><strong>${esc(h.summary)}</strong><p>${fmt(h.timestamp)} · ${esc(this.records.find(r=>r.id===h.notification_id)?.name || "Deleted notification")}</p></div><span class="badge">${esc(h.event)}</span></div>`).join("")}</div>`:this.empty("No history matches", "Meaningful matches, deliveries, pauses, resolutions, and expiries appear here.");
  }
  empty(title, text) { return `<div class="empty"><div class="empty-icon">🔔</div><h2>${title}</h2><p>${text}</p>${this.tab==="active"?`<button class="primary" id="empty-create">Create your first notification</button>`:""}</div>`; }
  styles() { return `<style>
    :host{display:block;min-height:100vh;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,Arial,sans-serif);box-sizing:border-box;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}*{box-sizing:border-box}button,input,select,textarea{font:inherit;color:inherit}button{cursor:pointer}.page{max-width:1180px;margin:auto;padding:28px 24px 64px}.hero{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:22px}.hero h1{font-size:30px;margin:0 0 6px;letter-spacing:-.4px}.hero p{margin:0;color:var(--secondary-text-color)}.primary,.secondary{border:0;border-radius:12px;padding:12px 18px;min-height:44px;font-weight:600}.primary{background:var(--primary-color);color:var(--text-primary-color,#fff);box-shadow:0 4px 14px color-mix(in srgb,var(--primary-color) 28%,transparent)}.secondary{background:var(--secondary-background-color);border:1px solid var(--divider-color)}.toolbar{display:flex;align-items:center;gap:12px;margin:18px 0}.search{flex:1;position:relative}.search input{width:100%;height:46px;border:1px solid var(--divider-color);border-radius:13px;background:var(--card-background-color);padding:0 16px 0 42px}.search:before{content:'⌕';position:absolute;left:16px;top:11px;color:var(--secondary-text-color)}.tabs{display:flex;gap:4px;border-bottom:1px solid var(--divider-color);overflow:auto}.tab{background:none;border:0;padding:14px 16px;color:var(--secondary-text-color);white-space:nowrap;border-bottom:3px solid transparent}.tab.active{color:var(--primary-color);border-color:var(--primary-color);font-weight:600}.count{background:var(--secondary-background-color);border-radius:99px;padding:2px 7px;font-size:12px;margin-left:5px}.content{padding-top:20px}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}.card{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:18px;padding:18px;box-shadow:var(--ha-card-box-shadow,0 2px 8px rgba(0,0,0,.08));transition:transform .15s,box-shadow .15s}.card:hover{transform:translateY(-1px);box-shadow:0 7px 22px rgba(0,0,0,.11)}.card-top{display:flex;align-items:flex-start;gap:12px}.grow{flex:1;min-width:0}.card h3{margin:0 0 5px;font-size:17px}.card p{margin:0;color:var(--secondary-text-color);line-height:1.45}.status-icon{color:var(--success-color,#43a047);font-size:13px;padding-top:4px}.status-icon.paused,.status-icon.disabled{color:var(--warning-color,#f9a825)}.status-icon.expired{color:var(--secondary-text-color)}.status-icon.active{color:var(--error-color,#db4437)}.badge{background:var(--secondary-background-color);border-radius:99px;padding:5px 9px;font-size:12px;white-space:nowrap;text-transform:capitalize}.icon{border:0;background:transparent;min-width:40px;min-height:40px;border-radius:50%;font-size:20px}.icon:hover{background:var(--secondary-background-color)}.meta{display:flex;flex-wrap:wrap;gap:8px 16px;border-top:1px solid var(--divider-color);margin-top:16px;padding-top:13px;color:var(--secondary-text-color);font-size:12px}.quick{display:flex;gap:6px;margin-top:13px;flex-wrap:wrap}.quick button{border:0;background:var(--secondary-background-color);border-radius:9px;padding:8px 11px}.danger{color:var(--error-color,#db4437)!important}.empty{text-align:center;padding:70px 20px;color:var(--secondary-text-color)}.empty h2{color:var(--primary-text-color)}.empty-icon{font-size:45px;filter:grayscale(.2);opacity:.65}.timeline{max-width:850px}.history-item{display:flex;align-items:flex-start;gap:14px;padding:15px 6px;border-bottom:1px solid var(--divider-color)}.history-item>div{flex:1}.history-item p{margin:5px 0 0;color:var(--secondary-text-color);font-size:13px}.dot{width:10px;height:10px;margin-top:5px;border-radius:50%;background:var(--primary-color)}.scrim{position:fixed;z-index:10;inset:0;background:rgba(0,0,0,.55);display:grid;place-items:center;padding:18px}.dialog{width:min(820px,100%);max-height:calc(100vh - 36px);background:var(--card-background-color);border-radius:22px;display:flex;flex-direction:column;box-shadow:0 24px 80px rgba(0,0,0,.35);overflow:hidden}.dialog header,.dialog footer{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;border-bottom:1px solid var(--divider-color);gap:16px}.dialog header h2{margin:0;font-size:22px}.dialog header p{margin:4px 0 0;color:var(--secondary-text-color)}.dialog footer{border-top:1px solid var(--divider-color);border-bottom:0;justify-content:flex-end}.editor-body{overflow:auto;padding:0 24px 28px}.editor-body>section{padding:22px 0;border-bottom:1px solid var(--divider-color)}.editor-body h3{margin:0 0 15px;font-size:16px}.subcard{border:1px solid var(--divider-color);border-radius:14px;padding:15px;margin-bottom:12px;background:var(--primary-background-color)}.subhead{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}label{display:flex;flex-direction:column;gap:7px;font-size:13px;font-weight:500;margin:12px 0}input,select,textarea{width:100%;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color);padding:11px 12px;min-height:43px}textarea{resize:vertical}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.check{flex-direction:row;align-items:center;font-weight:400}.check input,.choice input{width:20px;min-height:20px}.choice-row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.choice{flex-direction:row;align-items:flex-start;border:1px solid var(--divider-color);border-radius:12px;padding:12px;margin:0}.choice span{display:flex;flex-direction:column;gap:3px}.choice small,label small{font-weight:400;color:var(--secondary-text-color);line-height:1.3}.error{color:var(--error-color,#db4437);font-size:12px;margin-top:5px}.muted{color:var(--secondary-text-color)}details{padding:20px 0;border-bottom:1px solid var(--divider-color)}summary{font-weight:600;cursor:pointer}.advanced{padding-top:10px}.preview{background:color-mix(in srgb,var(--primary-color) 8%,var(--card-background-color));padding:18px!important;margin-top:20px;border-radius:14px;border:1px solid color-mix(in srgb,var(--primary-color) 22%,var(--divider-color))!important}.preview div{margin:7px 0;line-height:1.45}.toast{position:fixed;z-index:20;bottom:24px;left:50%;transform:translateX(-50%);background:var(--primary-text-color);color:var(--primary-background-color);padding:12px 18px;border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.25)}.skeleton{height:120px;border-radius:18px;background:linear-gradient(90deg,var(--card-background-color),var(--secondary-background-color),var(--card-background-color));background-size:200%;animation:pulse 1.4s infinite}@keyframes pulse{to{background-position:-200% 0}}@media(max-width:700px){.page{padding:18px 14px 40px}.hero{align-items:stretch}.hero h1{font-size:24px}.hero .primary{font-size:0;width:48px;padding:0}.hero .primary:after{content:'+';font-size:26px}.cards{grid-template-columns:1fr}.toolbar{flex-wrap:wrap}.tabs{order:2;width:100%}.scrim{padding:0;place-items:end center}.dialog{max-height:96vh;border-radius:20px 20px 0 0}.dialog header,.dialog footer{padding:16px}.editor-body{padding:0 16px 20px}.grid,.choice-row{grid-template-columns:1fr}.quick button{flex:1}.card{padding:15px}.badge{display:none}}
    .delivery-help{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px 32px;color:var(--secondary-text-color)}.delivery-help a{color:var(--primary-color);font-weight:600;white-space:nowrap}.notify-targets{display:flex;flex-direction:column;gap:9px;margin:14px 0}.notify-targets>small,.legacy-note{color:var(--secondary-text-color)}.picker-row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px}.picker-row .icon{align-self:center}.notify-targets .secondary{align-self:flex-start}.weekdays{display:flex;flex-wrap:wrap;gap:4px 14px;border:1px solid var(--divider-color);border-radius:10px;padding:10px 12px}.weekdays legend{font-size:13px;font-weight:500}.weekdays label{margin:3px 0}
  </style>`; }

  render() {
    if (!this.shadowRoot) return;
    this.captureEditorState();
    const counts = {active:this.records.filter(r=>!r.paused&&r.enabled&&r.status!=="expired").length,paused:this.records.filter(r=>r.paused||r.status==="disabled").length,history:this.history.length,expired:this.records.filter(r=>r.status==="expired").length};
    const items = this.filtered();
    this.shadowRoot.innerHTML = `${this.styles()}<main class="page"><div class="hero"><div><h1>Conditional Notifications</h1><p>Notify me when something happens.</p></div><button class="primary" id="create">+ Create notification</button></div><div class="toolbar"><div class="search"><input id="search" aria-label="Search conditional notifications" placeholder="Search names, entities, or descriptions" value="${esc(this.search)}"></div></div><nav class="tabs" aria-label="Notification views">${["active","paused","history","expired"].map(t=>`<button class="tab ${this.tab===t?"active":""}" data-tab="${t}">${t[0].toUpperCase()+t.slice(1)} <span class="count">${counts[t]}</span></button>`).join("")}</nav><section class="content">${this.loading?`<div class="cards"><div class="skeleton"></div><div class="skeleton"></div></div>`:this.tab==="history"?this.renderHistory():items.length?`<div class="cards">${items.map(r=>this.renderCard(r)).join("")}</div>`:this.empty(this.tab==="active"?"Nothing is watching yet":`No ${this.tab} notifications`,this.tab==="active"?"Create a one-time or repeating watch in a few selections.":"Items will appear here when their status changes.")}</section></main>${this.renderEditor()}${this.toast?`<div class="toast" role="status">${esc(this.toast)}</div>`:""}`;
    this.hydrateEditor(); this.bind(); this.bindHass();
    this.restoreEditorState();
  }
  bindHass() { if (!this.shadowRoot || !this.hass) return; this.shadowRoot.querySelectorAll("ha-entity-picker").forEach(el=>{el.hass=this.hass;if(!el._conditionalNotificationsReady){el.value=el.dataset.value||"";if(el.dataset.domain)el.includeDomains=[el.dataset.domain];el._conditionalNotificationsReady=true;}}); }
  bind() {
    const root=this.shadowRoot;
    root.querySelector("#create")?.addEventListener("click",()=>this.openEditor()); root.querySelector("#empty-create")?.addEventListener("click",()=>this.openEditor());
    root.querySelectorAll("[data-tab]").forEach(x=>x.addEventListener("click",()=>{this.tab=x.dataset.tab;this.render();}));
    root.querySelector("#search")?.addEventListener("input",e=>{this.search=e.target.value;clearTimeout(this.searchTimer);this.searchTimer=setTimeout(()=>this.render(),150);});
    root.querySelectorAll("[data-action]").forEach(x=>x.addEventListener("click",e=>{e.stopPropagation();this.action(x.dataset.id,x.dataset.action);}));
    root.querySelectorAll("[data-edit]").forEach(x=>x.addEventListener("click",e=>{e.stopPropagation();this.openEditor(this.records.find(r=>r.id===x.dataset.edit));}));
    root.querySelectorAll("[data-open]").forEach(x=>x.addEventListener("keydown",e=>{if(e.key==="Enter")this.openEditor(this.records.find(r=>r.id===x.dataset.open));}));
    if (!this.editor) return;
    root.querySelectorAll("[data-path]").forEach(x=>{x.addEventListener("input",e=>this.onField(e));x.addEventListener("change",e=>this.onField(e));x.addEventListener("value-changed",e=>this.onField(e));});
    root.querySelectorAll("[data-trigger-type]").forEach(x=>x.addEventListener("change",()=>this.changeTrigger(Number(x.dataset.triggerType),x.value)));
    root.querySelectorAll("[data-condition-type]").forEach(x=>x.addEventListener("change",()=>this.changeCondition(Number(x.dataset.conditionType),x.value)));
    root.querySelectorAll("[data-condition-weekday]").forEach(x=>x.addEventListener("change",()=>this.toggleConditionWeekday(Number(x.dataset.conditionWeekday),x.value,x.checked)));
    root.querySelectorAll("[data-remove-trigger]").forEach(x=>x.addEventListener("click",()=>this.removeTrigger(Number(x.dataset.removeTrigger))));
    root.querySelectorAll("[data-remove-condition]").forEach(x=>x.addEventListener("click",()=>this.removeCondition(Number(x.dataset.removeCondition))));
    root.querySelectorAll("[data-remove-notify-entity]").forEach(x=>x.addEventListener("click",()=>this.removeNotifyEntity(Number(x.dataset.removeNotifyEntity))));
    root.querySelector("#add-trigger")?.addEventListener("click",()=>this.addTrigger()); root.querySelector("#add-condition")?.addEventListener("click",()=>this.addCondition());
    root.querySelector("#add-notify-entity")?.addEventListener("click",()=>this.addNotifyEntity());
    root.querySelector(".dialog details")?.addEventListener("toggle",e=>this.setAdvancedOpen(e.currentTarget.open));
    root.querySelector("#recurring-toggle")?.addEventListener("change",e=>this.toggleRecurring(e.target.checked)); root.querySelector("#resolve-toggle")?.addEventListener("change",e=>this.toggleResolve(e.target.checked)); root.querySelector("#weekdays")?.addEventListener("input",e=>this.setWeekdays(e.target.value));
    root.querySelector("#save-editor")?.addEventListener("click",()=>this.save()); root.querySelector("#cancel-editor")?.addEventListener("click",()=>this.closeEditor()); root.querySelector("#close-editor")?.addEventListener("click",()=>this.closeEditor());
    root.querySelector(".scrim")?.addEventListener("click",e=>{if(e.target.classList.contains("scrim"))this.closeEditor();});
  }
  renderToast() {
    const current = this.shadowRoot?.querySelector(".toast");
    if (!this.toast) { current?.remove(); return; }
    if (current) { current.textContent = this.toast; return; }
    const toast = document.createElement("div");
    toast.className = "toast"; toast.setAttribute("role", "status"); toast.textContent = this.toast;
    this.shadowRoot?.append(toast);
  }
  showToast(message) {
    this.toast=message; this.renderToast(); clearTimeout(this.toastTimer);
    this.toastTimer=setTimeout(()=>{this.toast="";this.renderToast();},3500);
  }
}

if (!customElements.get("conditional-notifications-panel")) customElements.define("conditional-notifications-panel", ConditionalNotificationsPanel);
export { ConditionalNotificationsPanel };
