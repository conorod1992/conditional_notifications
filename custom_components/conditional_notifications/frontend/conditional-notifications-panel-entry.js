const lifecycleUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-lifecycle.js"
  : "/conditional_notifications_panel_lifecycle.js";

const { ConditionalNotificationsPanel } = await import(lifecycleUrl);
const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  const helpers = this.shadowRoot?.querySelectorAll(".delivery-help") || [];
  if (helpers.length > 1) helpers[0].remove();
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
