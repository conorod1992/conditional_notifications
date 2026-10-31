import { ESLint } from "eslint";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const eslint = new ESLint({
  cwd: root,
  overrideConfigFile: resolve(root, "frontend/eslint.config.mjs"),
});
const results = await eslint.lintFiles([
  "custom_components/conditional_notifications/frontend/*.js",
  "frontend/{tests,browser,scripts}/*.mjs",
  "frontend/*.mjs",
]);
const formatter = await eslint.loadFormatter("stylish");
const output = formatter.format(results);
if (output) process.stdout.write(output);
process.exitCode = results.some((result) => result.errorCount) ? 1 : 0;
