"""Apply the follow-up native editor intrinsic-width containment fix."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "custom_components" / "conditional_notifications" / "frontend" / "conditional-notifications-panel-native-automation.js"
TEST = ROOT / "frontend" / "tests" / "native-automation.test.mjs"

source = NATIVE.read_text(encoding="utf-8")

anchor = '''function entityLabel(instance, entityId) {\n'''
helper = '''const NATIVE_AUTOMATION_WIDTH_HOSTS = new Set([\n  "ha-selector-trigger",\n  "ha-selector-condition",\n  "ha-automation-trigger",\n  "ha-automation-condition",\n  "ha-sortable",\n  "ha-automation-trigger-row",\n  "ha-automation-condition-row",\n  "ha-card",\n  "ha-expansion-panel",\n  "ha-automation-trigger-editor",\n  "ha-automation-condition-editor",\n  "ha-form",\n]);\n\nfunction constrainNativeAutomationTree(root) {\n  if (!root?.querySelectorAll) return;\n  for (const element of root.querySelectorAll("*")) {\n    if (NATIVE_AUTOMATION_WIDTH_HOSTS.has(element.localName)) {\n      element.style.minWidth = "0";\n      element.style.maxWidth = "100%";\n      element.style.width = "100%";\n      element.style.boxSizing = "border-box";\n    }\n    if (element.shadowRoot) constrainNativeAutomationTree(element.shadowRoot);\n  }\n}\n\nfunction normalizeNativeAutomationLayout(selector) {\n  if (!selector) return;\n  selector.style.minWidth = "0";\n  selector.style.maxWidth = "100%";\n  selector.style.width = "100%";\n  selector.style.boxSizing = "border-box";\n\n  const apply = () => constrainNativeAutomationTree(selector.shadowRoot);\n  queueMicrotask(apply);\n  if (typeof requestAnimationFrame === "function") {\n    requestAnimationFrame(() => {\n      apply();\n      requestAnimationFrame(apply);\n    });\n  }\n}\n\n'''
if helper not in source:
    if anchor not in source:
        raise RuntimeError("Could not find layout helper insertion point")
    source = source.replace(anchor, helper + anchor, 1)

source = source.replace(
    '    .native-automation-editor{margin:12px 0 4px;min-width:0;max-width:100%;overflow-x:auto;overscroll-behavior-inline:contain}\n'
    '    .native-automation-selector{display:block;width:100%;max-width:100%;min-width:0;box-sizing:border-box}\n',
    '    .native-automation-editor{margin:12px 0 4px;min-width:0;max-width:100%;width:100%;box-sizing:border-box;contain:inline-size;overflow-x:auto;overscroll-behavior-inline:contain}\n'
    '    .native-automation-selector{display:block;width:100%;max-width:100%;min-width:0;box-sizing:border-box;contain:inline-size}\n',
    1,
)

source = source.replace(
    '''  const matchCurrent = this.shadowRoot.querySelector('[data-path="match_current_state"]');\n''',
    '''  this.shadowRoot.querySelectorAll(".native-automation-selector")\n    .forEach(normalizeNativeAutomationLayout);\n\n  const matchCurrent = this.shadowRoot.querySelector('[data-path="match_current_state"]');\n''',
    1,
)

for old, new in [
    (
        '    syncNativeAutomationSelectorValue(event.currentTarget, replacement);\n',
        '    syncNativeAutomationSelectorValue(event.currentTarget, replacement);\n    normalizeNativeAutomationLayout(event.currentTarget);\n',
    ),
    (
        '    syncNativeAutomationSelectorValue(event.currentTarget, definition.conditions);\n',
        '    syncNativeAutomationSelectorValue(event.currentTarget, definition.conditions);\n    normalizeNativeAutomationLayout(event.currentTarget);\n',
    ),
    (
        '    syncNativeAutomationSelectorValue(event.currentTarget, triggers);\n',
        '    syncNativeAutomationSelectorValue(event.currentTarget, triggers);\n    normalizeNativeAutomationLayout(event.currentTarget);\n',
    ),
]:
    source = source.replace(old, new, 1)

source = source.replace(
    '''  mergeNativeTriggers,\n  simpleCurrentStateCandidate,\n''',
    '''  constrainNativeAutomationTree,\n  mergeNativeTriggers,\n  normalizeNativeAutomationLayout,\n  simpleCurrentStateCandidate,\n''',
    1,
)

required = [
    "contain:inline-size",
    "function constrainNativeAutomationTree(root)",
    "function normalizeNativeAutomationLayout(selector)",
    '.forEach(normalizeNativeAutomationLayout);',
    "normalizeNativeAutomationLayout(event.currentTarget);",
]
for item in required:
    if item not in source:
        raise RuntimeError(f"Overflow follow-up did not apply: {item}")

NATIVE.write_text(source, encoding="utf-8")

test_source = TEST.read_text(encoding="utf-8")
test_source = test_source.replace(
    '''const {\n  mergeNativeTriggers,\n''',
    '''const {\n  constrainNativeAutomationTree,\n  mergeNativeTriggers,\n''',
    1,
)

new_test = '''\n\ntest("embedded native automation hosts are constrained to their container", () => {\n  const card = {localName:"ha-card", style:{}, shadowRoot:null};\n  const ignored = {localName:"span", style:{}, shadowRoot:null};\n  const nestedRoot = {querySelectorAll:() => [card, ignored]};\n  const row = {localName:"ha-automation-trigger-row", style:{}, shadowRoot:nestedRoot};\n  const root = {querySelectorAll:() => [row]};\n\n  constrainNativeAutomationTree(root);\n\n  assert.equal(row.style.minWidth, "0");\n  assert.equal(row.style.maxWidth, "100%");\n  assert.equal(row.style.width, "100%");\n  assert.equal(row.style.boxSizing, "border-box");\n  assert.equal(card.style.maxWidth, "100%");\n  assert.deepEqual(ignored.style, {});\n});\n'''
if 'test("embedded native automation hosts are constrained to their container"' not in test_source:
    test_source += new_test

TEST.write_text(test_source, encoding="utf-8")
