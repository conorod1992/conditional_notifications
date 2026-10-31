import { test, expect } from "@playwright/test";

const definition = (extra = {}) => ({
  name: "Door watch",
  triggers: [{ trigger: "state", entity_id: "binary_sensor.door", to: "on" }],
  conditions: [],
  title: "Door",
  message: "Opened",
  repeat_policy: "every",
  delivery: { use_defaults: true },
  ...extra,
});
const record = (id = "one", extra = {}) => ({
  id,
  name: extra.name || "Door watch",
  enabled: true,
  paused: false,
  status: "watching",
  revision: 1,
  notification_count: 0,
  currently_active: true,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  definition: definition(extra),
});

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) => {
    throw error;
  });
  await page.goto("/");
  await page.waitForFunction(() => typeof window.boot === "function");
});

async function boot(page, options = {}) {
  await page.evaluate((options) => window.boot(options), options);
  await expect(page.locator("#create")).toBeVisible();
  await expect(page.locator(".skeleton")).toHaveCount(0);
}
async function emit(page, id, value) {
  await page.evaluate(
    ({ id, value }) => {
      const selector = window.panel.shadowRoot.getElementById(id);
      selector.value = value;
      selector.dispatchEvent(
        new CustomEvent("value-changed", {
          detail: { value },
          bubbles: true,
          composed: true,
        }),
      );
    },
    { id, value },
  );
}

test("named rows keep their identity after a preceding native trigger is removed", async ({
  page,
}) => {
  const triggers = [
    { trigger: "state", entity_id: "binary_sensor.a", to: "on" },
    { trigger: "state", entity_id: "binary_sensor.b", to: "on" },
    { type: "named", trigger_id: "external_one" },
    { type: "named", trigger_id: "external_two" },
  ];
  await boot(page, { records: [record("one", { triggers })] });
  await page.locator('[data-edit="one"]').click();
  await emit(page, "native-ha-triggers", [triggers[1]]);
  await page.locator('[data-external-trigger-index="3"]').fill("changed_two");
  await page.locator('[data-remove-external-trigger="2"]').click();
  const names = await page.evaluate(() =>
    window.panel.editor.definition.triggers
      .filter((t) => t.type === "named")
      .map((t) => t.trigger_id),
  );
  expect(names).toEqual(["changed_two"]);
});

test("discarded resolution drafts cannot block the next notification", async ({
  page,
}) => {
  await boot(page, {
    records: [
      record("one", {
        resolve_when: {
          trigger: "state",
          entity_id: "binary_sensor.door",
          to: "off",
        },
      }),
      record("two", { name: "Other watch" }),
    ],
  });
  await page.locator('[data-edit="one"]').click();
  await emit(page, "native-ha-resolution", []);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#cancel-editor").click();
  await page.locator('[data-edit="two"]').click();
  await page.locator("#save-editor").click();
  await expect(page.locator("#save-editor")).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      window.calls.some(
        (c) => c.type.endsWith("/update") && c.notification_id === "two",
      ),
    ),
  ).toBe(true);
});

test("untouched limited-count default is sent as three", async ({ page }) => {
  await boot(page, { records: [record()] });
  await page.locator('[data-edit="one"]').click();
  await page.locator('input[value="limited"]').check();
  await expect(page.locator('[data-path="max_notifications"]')).toHaveValue(
    "3",
  );
  await page.locator("#save-editor").click();
  expect(
    await page.evaluate(
      () =>
        window.calls.find((c) => c.type.endsWith("/update")).changes
          .max_notifications,
    ),
  ).toBe(3);
});

test("keyboard actions invoke the button and card activation still opens details", async ({
  page,
}) => {
  await boot(page, { records: [record()] });
  const button = page.locator('.card [data-action="test"]');
  await button.focus();
  await page.keyboard.press("Enter");
  await expect
    .poll(() =>
      page.evaluate(
        () => window.calls.filter((c) => c.action === "test").length,
      ),
    )
    .toBe(1);
  await expect(page.locator(".detail-dialog")).toHaveCount(0);
  await page.locator(".card").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".detail-dialog")).toBeVisible();
});

test("native edits update correlation and current-state eligibility immediately", async ({
  page,
}) => {
  await boot(page, { records: [record()] });
  await page.locator('[data-edit="one"]').click();
  await expect(
    page.locator('#trigger-match-mode option[value="all_within"]'),
  ).toHaveJSProperty("disabled", true);
  await emit(page, "native-ha-triggers", [
    { trigger: "time", at: "09:00:00" },
    { trigger: "time", at: "10:00:00" },
  ]);
  await expect(
    page.locator('#trigger-match-mode option[value="all_within"]'),
  ).toHaveJSProperty("disabled", false);
  await expect(
    page.locator('[data-path="match_current_state"]'),
  ).toBeDisabled();
});

test("search retains focus and caret after the debounce render", async ({
  page,
}) => {
  await boot(page, { records: [record()] });
  await page.locator("#search").fill("Door");
  await expect(page.locator("#search")).toBeFocused();
  await page.waitForTimeout(250);
  await expect(page.locator("#search")).toBeFocused();
  expect(
    await page.locator("#search").evaluate((el) => el.selectionStart),
  ).toBe(4);
});

test("history can show all retained records", async ({ page }) => {
  const history = Array.from({ length: 420 }, (_, i) => ({
    id: String(i),
    notification_id: "one",
    event: "matched",
    summary: `Match ${i}`,
    timestamp: "2026-10-01T00:00:00Z",
  }));
  await boot(page, { records: [record()], history });
  await page.locator('[data-tab="history"]').click();
  await expect(page.locator("#more-history")).toBeVisible();
  await page.locator("#more-history").click();
  await page.locator("#more-history").click();
  await expect(page.locator("#more-history")).toHaveCount(0);
  await expect(page.locator(".history-item")).toHaveCount(420);
});

test("server validation remains visible in the relevant section", async ({
  page,
}) => {
  await boot(page, { records: [record()] });
  await page.locator('[data-edit="one"]').click();
  await page.evaluate(() => (window.rejectSave = true));
  await page.locator("#save-editor").click();
  await expect(page.locator(".editor-error-summary")).toContainText(
    "When: Invalid duration",
  );
  await expect(page.locator(".editor-error-summary")).toBeFocused();
});

test("modal focus wraps and restores to the create button", async ({
  page,
}) => {
  await boot(page);
  await page.locator("#create").click();
  await page.locator("#save-editor").focus();
  await page.keyboard.press("Tab");
  await expect(page.locator("#close-editor")).toBeFocused();
  await page.locator("#cancel-editor").click();
  await expect(page.locator("#create")).toBeFocused();
});

test("repair status opens the existing definition and uses the recovery endpoint", async ({
  page,
}) => {
  await boot(page, {
    quarantined: [
      {
        id: "broken",
        name: "Broken watch",
        reason: "Validation failed at triggers.0",
        definition: definition(),
      },
    ],
  });
  await page.getByText("1 notification(s) need repair").click();
  await page.getByRole("button", { name: "Inspect and repair" }).click();
  await page.locator("#save-editor").click();
  expect(
    await page.evaluate(() =>
      window.calls.some(
        (c) => c.type.endsWith("/recover") && c.notification_id === "broken",
      ),
    ),
  ).toBe(true);
});

test("delivery UI exposes compatible services and labels both timezones", async ({
  page,
}) => {
  await boot(page, { records: [record()] });
  await page.locator('[data-edit="one"]').click();
  await page.locator('[data-path="delivery.use_defaults"]').uncheck();
  await expect(page.locator("#mobile-app-services")).toHaveCount(1);
  await expect(page.locator(".companion-compatibility")).toContainText(
    "title and message only",
  );
  await expect(page.locator(".timezone-note")).toContainText("Europe/Dublin");
});
