import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const target = resolve("../custom_components/conditional_notifications/frontend/conditional-notifications-panel.js");
const source = await readFile(target, "utf8");
if (!source.includes('customElements.define("conditional-notifications-panel"')) {
  throw new Error("Panel entrypoint is incomplete");
}
// Normalize the committed dependency-free ES module. Building twice is intentionally idempotent.
await writeFile(target, source.replace(/\r\n/g, "\n"));
console.log(`Verified and built ${target} (${source.length} bytes)`);
