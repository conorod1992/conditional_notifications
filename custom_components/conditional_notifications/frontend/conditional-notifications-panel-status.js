import { ConditionalNotificationsPanel } from "./conditional-notifications-panel.js";

const panel = ConditionalNotificationsPanel.prototype;
const originalRender = panel.render;
const originalBind = panel.bind;
const originalHydrateEditor = panel.hydrateEditor;
const originalRenderHistory = panel.renderHistory;

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
const label = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());

panel.eligibilitySummary = function(record, now = new Date()) {
  if (record.status === "expired") return {state:"Expired", detail:"The expiry deadline has passed, so this notification is no longer watching."};
  if (!record.enabled) return {state:"Disabled", detail:"This notification is disabled and will not accept triggers."};
  if (record.paused) return {state:"Paused", detail:"This notification is paused. Its expiry deadline, if any, still continues."};

  const available = record.definition?.available_from ? new Date(record.definition.available_from) : null;
  if (available && available > now) return {state:"Waiting to start", detail:`It becomes available ${fmt(record.definition.available_from)}.`};
  if (!record.currently_active) return {state:"Outside active period", detail:"The notification is enabled, but the current time is outside its configured active period."};

  const nextEligible = record.next_eligible_at ? new Date(record.next_eligible_at) : null;
  if (nextEligible && nextEligible > now) return {state:"Cooling down", detail:`Another trigger can be accepted after ${fmt(record.next_eligible_at)}.`};
  if (record.active_occurrence) return {state:"Problem active", detail:"A qualifying occurrence has been delivered and is currently active pending resolution."};
  return {state:"Ready", detail:"A qualifying trigger can be accepted now."};
};

panel.historyForRecord = function(recordId, limit = 25) {
  return (this.history || []).filter(item => item.notification_id === recordId).slice(0, limit);
};

panel.deliverySummary = function(results) {
  const rows = Array.isArray(results) ? results : [];
  if (!rows.length) return "No delivery attempt has been recorded yet.";
  const successes = rows.filter(item => item.success).length;
  if (successes === rows.length) return `All ${rows.length} configured delivery channel${rows.length === 1 ? "" : "s"} succeeded.`;
  if (!successes) return `All ${rows.length} attempted delivery channel${rows.length === 1 ? "" : "s"} failed.`;
  return `${successes} of ${rows.length} delivery channels succeeded.`;
};

panel.compactDetails = function(details) {
  if (!details || typeof details !== "object" || !Object.keys(details).length) return "";
  return Object.entries(details).map(([key,value]) => {
    const shown = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
    return `<span><strong>${esc(label(key))}:</strong> ${esc(shown)}</span>`;
  }).join("");
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  const resolve = this.editor?.definition?.resolve_when;
  if (!resolve || resolve.type !== "state") return;
  if (this.shadowRoot.querySelector('[data-path="resolve_when.for"]')) return;
  const anchor = this.shadowRoot.querySelector('[data-path="resolve_when.to"]')?.closest("label");
  anchor?.insertAdjacentHTML(
    "afterend",
    `<label>Resolution minimum duration (seconds)<input type="number" min="0" data-path="resolve_when.for" value="${resolve.for || ""}"><small>Only resolve if the clearing state remains true for this long. Leave blank for immediate resolution.</small></label>`,
  );
};

panel.openDetails = function(record) {
  this.detailId = record?.id || null;
  this._focusDetailsOnRender = Boolean(this.detailId);
  this.render();
};

panel.closeDetails = function() {
  this.detailId = null;
  this.render();
};

panel.renderCard = function(record) {
  const trigger = record.definition.triggers.map(t=>this.triggerSummary(t)).join(" or ");
  const eligibility = this.eligibilitySummary(record);
  return `<article class="card status-card" tabindex="0" data-details-card="${record.id}" aria-label="View details for ${esc(record.name)}"><div class="card-top"><div class="status-icon ${record.status}">●</div><div class="grow"><h3>${esc(record.name)}</h3><p>${esc(trigger)}</p></div><span class="badge">${esc(record.status)}</span></div><div class="meta"><span>🔔 ${record.notification_count} sent${record.remaining_notifications!==null?` · ${record.remaining_notifications} remaining`:""}</span><span>◷ ${esc(eligibility.state)}</span>${record.definition.expires_at?`<span>⌛ ${fmt(record.definition.expires_at)}</span>`:""}${record.last_ignored_reason?`<span>ⓘ Last ignored: ${esc(record.last_ignored_reason)}</span>`:""}</div><div class="quick"><button data-details="${record.id}">Details</button><button data-action="${record.paused?"resume":"pause"}" data-id="${record.id}">${record.paused?"Resume":"Pause"}</button><button data-action="test" data-id="${record.id}">Test</button><button data-action="duplicate" data-id="${record.id}">Duplicate</button><button data-edit="${record.id}">Edit</button><button class="danger" data-action="delete" data-id="${record.id}">Delete</button></div></article>`;
};

panel.renderDetailHistory = function(record) {
  const entries = this.historyForRecord(record.id);
  if (!entries.length) return `<p class="muted">No lifecycle history has been recorded for this notification yet.</p>`;
  return `<div class="detail-timeline">${entries.map(item=>`<div class="detail-history-item"><span class="dot"></span><div class="grow"><div class="detail-history-head"><strong>${esc(item.summary)}</strong><span class="badge">${esc(item.event)}</span></div><p>${fmt(item.timestamp)}</p>${item.details && Object.keys(item.details).length?`<details class="history-details"><summary>Technical details</summary><div class="detail-pairs">${this.compactDetails(item.details)}</div></details>`:""}</div></div>`).join("")}</div>`;
};

panel.renderDetails = function() {
  if (!this.detailId) return "";
  const record = this.records.find(item => item.id === this.detailId);
  if (!record) return "";

  const eligibility = this.eligibilitySummary(record);
  const preview = this.preview(record.definition);
  const deliveries = Array.isArray(record.last_delivery) ? record.last_delivery : [];
  const lastTrigger = record.last_trigger && typeof record.last_trigger === "object" ? record.last_trigger : null;
  const remaining = record.remaining_notifications === null ? "Not limited" : record.remaining_notifications;
  const resolution = record.definition.resolve_when
    ? (record.active_occurrence ? "Waiting for resolution" : `Configured: ${this.triggerSummary(record.definition.resolve_when)}`)
    : "Not configured";

  return `<div class="scrim detail-scrim" role="presentation"><div class="dialog detail-dialog" role="dialog" aria-modal="true" aria-labelledby="detail-title">
    <header><div><h2 id="detail-title">${esc(record.name)}</h2><p>${esc(record.description || "Notification status and troubleshooting")}</p></div><button class="icon" id="close-details" aria-label="Close details">×</button></header>
    <div class="editor-body detail-body">
      <section class="health ${eligibility.state.toLowerCase().replaceAll(" ","-")}"><div><span class="eyebrow">Current eligibility</span><h3>${esc(eligibility.state)}</h3><p>${esc(eligibility.detail)}</p></div><span class="badge">${esc(record.status)}</span></section>

      <section><h3>At a glance</h3><div class="detail-grid">
        <div><span>Notifications sent</span><strong>${record.notification_count}</strong></div>
        <div><span>Remaining</span><strong>${esc(remaining)}</strong></div>
        <div><span>Last trigger</span><strong>${record.last_trigger_at?fmt(record.last_trigger_at):"None yet"}</strong></div>
        <div><span>Next eligible</span><strong>${record.next_eligible_at?fmt(record.next_eligible_at):"Now"}</strong></div>
        <div><span>Expires</span><strong>${record.definition.expires_at?fmt(record.definition.expires_at):"No expiry"}</strong></div>
        <div><span>Resolution</span><strong>${esc(resolution)}</strong></div>
      </div></section>

      ${record.last_ignored_reason?`<section class="attention"><h3>Why the last match was ignored</h3><p>${esc(record.last_ignored_reason)}</p><small>This is the most recently recorded rejection reason; a newer qualifying trigger may still be accepted.</small></section>`:""}

      <section><h3>What it is watching</h3><div class="definition-summary">
        <div><strong>Triggers</strong><span>${esc(preview.watching)}</span></div>
        <div><strong>Conditions</strong><span>${esc(preview.conditions)}</span></div>
        <div><strong>Active period</strong><span>${esc(preview.active)}</span></div>
        <div><strong>Behaviour</strong><span>${esc(preview.behaviour)}</span></div>
        <div><strong>Resolution</strong><span>${esc(preview.resolution)}</span></div>
        <div><strong>Delivery</strong><span>${esc(preview.delivery)}</span></div>
      </div></section>

      <section><h3>Last delivery</h3><p class="muted">${esc(this.deliverySummary(deliveries))}</p>${deliveries.length?`<div class="delivery-results">${deliveries.map(item=>`<div class="delivery-result ${item.success?"success":"failed"}"><span>${item.success?"✓":"!"}</span><div><strong>${esc(item.channel || "Unknown channel")}</strong><p>${item.success?"Delivered successfully":esc(item.error || "Delivery failed")}</p></div></div>`).join("")}</div>`:""}</section>

      <section><h3>Last trigger details</h3>${lastTrigger?`<div class="detail-pairs">${this.compactDetails(lastTrigger)}</div>`:`<p class="muted">No trigger has been recorded yet.</p>`}</section>

      <section><div class="section-head"><div><h3>Recent history</h3><p class="muted">Lifecycle events for this notification only.</p></div></div>${this.renderDetailHistory(record)}</section>

      <section class="technical"><details><summary>Record information</summary><div class="detail-grid compact"><div><span>Created</span><strong>${fmt(record.created_at)}</strong></div><div><span>Updated</span><strong>${fmt(record.updated_at)}</strong></div><div><span>Revision</span><strong>${esc(record.revision)}</strong></div><div><span>ID</span><strong class="mono">${esc(record.id)}</strong></div></div></details></section>
    </div>
    <footer><button class="secondary" id="detail-edit">Edit</button><button class="secondary" id="detail-rearm">Re-arm</button><button class="secondary" data-action="test" data-id="${record.id}">Test</button><button class="secondary" data-action="${record.paused?"resume":"pause"}" data-id="${record.id}">${record.paused?"Resume":"Pause"}</button><button class="primary" id="close-details-footer">Close</button></footer>
  </div></div>`;
};

panel.renderHistory = function() {
  const entries = this.history.filter(h=>!this.search || JSON.stringify(h).toLowerCase().includes(this.search.toLowerCase()));
  if (!entries.length) return originalRenderHistory.call(this);
  return `<div class="timeline">${entries.slice(0,200).map(h=>`<div class="history-item"><span class="dot"></span><div><strong>${esc(h.summary)}</strong><p>${fmt(h.timestamp)} · ${esc(this.records.find(r=>r.id===h.notification_id)?.name || "Deleted notification")}</p>${h.details && Object.keys(h.details).length?`<details class="history-details"><summary>Details</summary><div class="detail-pairs">${this.compactDetails(h.details)}</div></details>`:""}</div><span class="badge">${esc(h.event)}</span></div>`).join("")}</div>`;
};

panel.bind = function() {
  originalBind.call(this);
  const root = this.shadowRoot;
  root.querySelectorAll("[data-details]").forEach(button=>button.addEventListener("click",event=>{
    event.stopPropagation();
    this.openDetails(this.records.find(record=>record.id===button.dataset.details));
  }));
  root.querySelectorAll("[data-details-card]").forEach(card=>{
    card.addEventListener("click",event=>{
      if (event.target.closest("button,a,input,select,textarea")) return;
      this.openDetails(this.records.find(record=>record.id===card.dataset.detailsCard));
    });
    card.addEventListener("keydown",event=>{
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.openDetails(this.records.find(record=>record.id===card.dataset.detailsCard));
      }
    });
  });
};

panel.bindDetails = function() {
  const root = this.shadowRoot;
  root.querySelector("#close-details")?.addEventListener("click",()=>this.closeDetails());
  root.querySelector("#close-details-footer")?.addEventListener("click",()=>this.closeDetails());
  root.querySelector("#detail-edit")?.addEventListener("click",()=>{
    const record = this.records.find(item=>item.id===this.detailId);
    this.detailId = null;
    if (record) this.openEditor(record);
  });
  root.querySelector("#detail-rearm")?.addEventListener("click",()=>{
    const record = this.records.find(item=>item.id===this.detailId);
    if (!record) return;
    if (!confirm("Re-arm this notification? Its notification count, cooldown, last trigger, delivery result, and active occurrence will be reset.")) return;
    this.action(record.id, "rearm");
  });
  root.querySelector(".detail-scrim")?.addEventListener("click",event=>{
    if (event.target.classList.contains("detail-scrim")) this.closeDetails();
  });
  root.querySelector(".detail-dialog")?.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    event.preventDefault();
    this.closeDetails();
  });
  root.querySelectorAll(".detail-dialog [data-action]").forEach(button=>button.addEventListener("click",event=>{
    event.stopPropagation();
    this.action(button.dataset.id, button.dataset.action);
  }));
};

panel.render = function() {
  originalRender.call(this);
  if (!this.detailId || !this.shadowRoot) return;
  const markup = this.renderDetails();
  if (!markup) {
    this.detailId = null;
    return;
  }
  this.shadowRoot.insertAdjacentHTML("beforeend", markup);
  if (this._focusDetailsOnRender) {
    this._focusDetailsOnRender = false;
    requestAnimationFrame(() => this.shadowRoot?.querySelector("#close-details")?.focus());
  }
  const style = document.createElement("style");
  style.textContent = `
    .status-card{cursor:pointer}.status-card:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    .detail-dialog{width:min(900px,100%)}.detail-body>section{padding:20px 0;border-bottom:1px solid var(--divider-color)}
    .health{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;background:color-mix(in srgb,var(--primary-color) 7%,var(--card-background-color));margin:18px 0 0;padding:18px!important;border:1px solid color-mix(in srgb,var(--primary-color) 20%,var(--divider-color))!important;border-radius:14px}.health h3{font-size:22px;margin:4px 0}.health p{margin:0;color:var(--secondary-text-color);line-height:1.5}.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:11px;color:var(--secondary-text-color);font-weight:700}
    .detail-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.detail-grid>div{display:flex;flex-direction:column;gap:5px;background:var(--primary-background-color);border:1px solid var(--divider-color);border-radius:11px;padding:12px}.detail-grid span,.definition-summary strong{font-size:12px;color:var(--secondary-text-color);font-weight:500}.detail-grid strong{line-height:1.35;overflow-wrap:anywhere}.detail-grid.compact{grid-template-columns:repeat(2,1fr)}
    .attention{background:color-mix(in srgb,var(--warning-color,#f9a825) 8%,var(--card-background-color));padding:16px!important;margin-top:18px;border:1px solid color-mix(in srgb,var(--warning-color,#f9a825) 28%,var(--divider-color))!important;border-radius:12px}.attention p{margin:7px 0}.attention small{color:var(--secondary-text-color)}
    .definition-summary{display:grid;gap:10px}.definition-summary>div{display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:start}.definition-summary span{line-height:1.45}
    .delivery-results{display:grid;gap:8px;margin-top:12px}.delivery-result{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--divider-color);border-radius:10px;padding:11px}.delivery-result>span{width:24px;height:24px;display:grid;place-items:center;border-radius:50%;background:var(--secondary-background-color);font-weight:700}.delivery-result.success>span{color:var(--success-color,#43a047)}.delivery-result.failed>span{color:var(--error-color,#db4437)}.delivery-result p{margin:3px 0 0;color:var(--secondary-text-color);font-size:12px;line-height:1.4}
    .detail-pairs{display:flex;flex-wrap:wrap;gap:7px}.detail-pairs>span{background:var(--secondary-background-color);border-radius:8px;padding:7px 9px;font-size:12px;overflow-wrap:anywhere;max-width:100%}.detail-timeline{display:grid}.detail-history-item{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--divider-color)}.detail-history-item:last-child{border-bottom:0}.detail-history-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.detail-history-item p{margin:4px 0;color:var(--secondary-text-color);font-size:12px}.history-details{padding:0;border:0;margin-top:7px}.history-details summary{font-size:12px;color:var(--primary-color)}.history-details .detail-pairs{margin-top:8px}.section-head{display:flex;justify-content:space-between;align-items:flex-start}.section-head h3,.section-head p{margin-top:0}.technical details{padding:0;border:0}.mono{font-family:monospace;font-size:12px}
    @media(max-width:700px){.detail-grid{grid-template-columns:1fr 1fr}.definition-summary>div{grid-template-columns:1fr;gap:3px}.detail-dialog footer{flex-wrap:wrap}.detail-dialog footer button{flex:1}.health{flex-direction:column}.detail-history-head{flex-direction:column;gap:6px}}
  `;
  this.shadowRoot.append(style);
  this.bindDetails();
};

export { ConditionalNotificationsPanel };
