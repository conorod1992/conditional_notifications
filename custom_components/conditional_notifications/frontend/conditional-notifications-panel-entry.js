const correlationUrl = typeof window === "undefined"
  ? "./conditional-notifications-panel-correlation.js"
  : "/conditional_notifications_panel_correlation.js";

const { ConditionalNotificationsPanel } = await import(correlationUrl);
const panel = ConditionalNotificationsPanel.prototype;
const originalHydrateEditor = panel.hydrateEditor;

panel.hydrateEditor = function() {
  originalHydrateEditor.call(this);
  const helpers = this.shadowRoot?.querySelectorAll(".delivery-help") || [];
  if (helpers.length > 1) helpers[0].remove();
};

export { ConditionalNotificationsPanel };
