import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const moduleGraph = [
  ["conditional-notifications-panel.js", null],
  ["conditional-notifications-panel-status.js", "conditional-notifications-panel.js"],
  ["conditional-notifications-panel-correlation.js", "conditional-notifications-panel-status.js"],
  ["conditional-notifications-panel-lifecycle.js", "conditional-notifications-panel-correlation.js"],
  ["conditional-notifications-panel-entry.js", "conditional-notifications-panel-lifecycle.js"],
  ["conditional-notifications-panel-native-automation.js", "conditional-notifications-panel-entry.js"],
  ["conditional-notifications-panel-editor-ux.js", "conditional-notifications-panel-native-automation.js"],
  ["conditional-notifications-panel-performance.js", "conditional-notifications-panel-editor-ux.js"],
];

const frontendDir = resolve("../custom_components/conditional_notifications/frontend");

for (const [filename, dependency] of moduleGraph) {
  const target = resolve(frontendDir, filename);
  const source = await readFile(target, "utf8");

  if (dependency) {
    const expectedImport = `import { ConditionalNotificationsPanel } from "./${dependency}";`;
    if (!source.startsWith(expectedImport)) {
      throw new Error(`${filename} must statically import ${dependency}`);
    }
    if (source.includes("await import(") || source.includes('typeof window === "undefined"')) {
      throw new Error(`${filename} still contains environment-specific module loading`);
    }
  } else if (!source.includes('customElements.define("conditional-notifications-panel"')) {
    throw new Error("Panel base module is incomplete");
  }

  // Normalize every committed dependency-free ES module. Building twice is
  // intentionally idempotent and CI checks that it produces no tracked diff.
  await writeFile(target, source.replace(/\r\n/g, "\n"));
}

console.log(`Verified and built ${moduleGraph.length} panel modules`);
