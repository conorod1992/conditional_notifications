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

export { ConditionalNotificationsPanel };
