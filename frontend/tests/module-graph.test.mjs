import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

const frontendDir = resolve(
  "../custom_components/conditional_notifications/frontend",
);
const moduleGraph = [
  [
    "conditional-notifications-panel-status.js",
    "conditional-notifications-panel.js",
  ],
  [
    "conditional-notifications-panel-correlation.js",
    "conditional-notifications-panel-status.js",
  ],
  [
    "conditional-notifications-panel-lifecycle.js",
    "conditional-notifications-panel-correlation.js",
  ],
  [
    "conditional-notifications-panel-entry.js",
    "conditional-notifications-panel-lifecycle.js",
  ],
  [
    "conditional-notifications-panel-native-automation.js",
    "conditional-notifications-panel-entry.js",
  ],
  [
    "conditional-notifications-panel-editor-ux.js",
    "conditional-notifications-panel-native-automation.js",
  ],
  [
    "conditional-notifications-panel-performance.js",
    "conditional-notifications-panel-editor-ux.js",
  ],
];

test("panel enhancement modules use one static relative import chain", async () => {
  for (const [filename, dependency] of moduleGraph) {
    const source = await readFile(resolve(frontendDir, filename), "utf8");
    const expectedImport = `import { ConditionalNotificationsPanel as BasePanel } from "./${dependency}";`;

    assert.ok(
      source.startsWith(expectedImport),
      `${filename} imports ${dependency}`,
    );
    assert.equal(
      source.includes("await import("),
      false,
      `${filename} avoids dynamic imports`,
    );
    assert.equal(
      source.includes('typeof window === "undefined"'),
      false,
      `${filename} has no browser-vs-test URL branch`,
    );
  }
});

test("only the final composed panel registers the custom element", async () => {
  const base = await readFile(
    resolve(frontendDir, "conditional-notifications-panel.js"),
    "utf8",
  );
  assert.equal(base.includes("customElements.define("), false);

  for (const [filename] of moduleGraph) {
    const source = await readFile(resolve(frontendDir, filename), "utf8");
    assert.equal(
      /customElements\.define\(\s*"conditional-notifications-panel"/.test(
        source,
      ),
      filename.endsWith("-performance.js"),
    );
  }
});
