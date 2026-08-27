const statusUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-status.js"
  : "/conditional_notifications_panel_status.js";
const { ConditionalNotificationsPanel } = await import(statusUrl);

const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;
const originalBind = panel.bind;
const originalPreview = panel.preview;

panel.preview = function(definition) {
  const preview = originalPreview.call(this, definition);
  if (definition.match === "all_within") {
    const triggers = definition.triggers.map(trigger => this.triggerSummary(trigger)).join("; and ");
    preview.watching = `All configured triggers within ${this.duration(definition.match_window || 0)}: ${triggers}`;
  }
  return preview;
};

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  if (!this.editor || this.shadowRoot.querySelector("#trigger-match-mode")) return;

  const definition = this.editor.definition;
  const anchor = this.shadowRoot.querySelector(".preview");
  if (!anchor) return;
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
};

export { ConditionalNotificationsPanel };
