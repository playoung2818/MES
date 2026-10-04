// Optional integration checks against tests/ui_fixture.py; never use a production URL.
// NODE_PATH=<dev node_modules> MES_BROWSER_EXECUTABLE=<optional Chrome path> node tests/ui-browser.cjs
const { chromium } = require("playwright");
const axe = require("axe-core");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const base = process.env.MES_UI_TEST_URL || "http://127.0.0.1:5057";
const output = process.env.MES_UI_TEST_OUTPUT || "/tmp/mes-ui-check";
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.MES_BROWSER_EXECUTABLE || undefined,
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    const errors = [],
      requests = [],
      checks = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (!request.url().startsWith(base)) requests.push(request.url());
    });
    const go = async (path) => {
      const response = await page.goto(base + path);
      assert.equal(
        response.headers()["x-mes-ui-fixture"],
        "isolated",
        "Use tests/ui_fixture.py, never a production server",
      );
      assert.equal(response.status(), 200, path);
    };
    for (const width of [1440, 390, 320, 768, 1024]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const [name, path] of [
        ["home", "/"],
        ["editor", "/so/SO-20261208"],
        ["manual", "/wo/new"],
        ["history", "/generated"],
        ["saved", "/generated/wo/fixture-saved"],
        ["revision", "/wo/fixture-saved/edit"],
        ["planning", "/production_planning"],
        ["review", "/production_planning/orders"],
        ["approval", "/production_planning/orders/SO-20261209"],
      ]) {
        await go(path);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        );
        assert.equal(overflow, false, `${name} overflows at ${width}`);
        if (width === 1440 || width === 390) {
          await page.addScriptTag({ content: axe.source });
          const result = await page.evaluate(async () =>
            (
              await axe.run(document, {
                runOnly: {
                  type: "tag",
                  values: ["wcag2a", "wcag2aa", "wcag21aa"],
                },
              })
            ).violations.map((v) => ({
              id: v.id,
              nodes: v.nodes.map((n) => ({
                html: n.html,
                summary: n.failureSummary,
              })),
            })),
          );
          checks.push({ name, width, accessibility: result });
          fs.writeFileSync(
            `${output}/report.json`,
            JSON.stringify(
              { checks, errors, externalRequests: requests },
              null,
              2,
            ),
          );
          await page.screenshot({
            path: `${output}/${name}-${width}.png`,
            fullPage: true,
          });
        }
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await go("/");
    assert.equal(await page.locator(".home-table thead th").count(), 3);
    assert.equal(await page.getByText("PO-48101", { exact: true }).count(), 0);
    await page
      .getByLabel("Search sales orders", { exact: true })
      .fill("Nuvo-9160GC");
    await page
      .getByLabel("Search sales orders", { exact: true })
      .press("Enter");
    await page.waitForURL("**/?q=Nuvo-9160GC");
    assert.equal(await page.locator(".home-table tbody tr").count(), 1);
    await page.setViewportSize({ width: 320, height: 1000 });
    await go("/?q=missing-fixture-order");
    assert.match(
      await page.locator(".home-table").textContent(),
      /No orders match/,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: `${output}/empty-home-320.png`,
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await go("/so/SO-20261208");
    const qty = page.locator("[data-entry-quantity]");
    const serials = page.locator(".serial-input");
    await qty.nth(1).fill("0");
    await serials.nth(0).fill("0001");
    assert.equal(
      await page.locator(".serial-feedback:not([hidden])").count(),
      0,
    );
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    assert.match(
      await page.locator(".serial-feedback").first().textContent(),
      /Add 1 line/,
    );
    assert.equal(await page.locator("#generated-table").count(), 0);
    await serials.nth(0).fill("SN1,SN2;SN3");
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    assert.match(
      await page.locator(".serial-feedback").first().textContent(),
      /Add 1 line/,
    );
    await serials.nth(0).fill("REPEATED\nREPEATED");
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    await page.locator("#generated-table").waitFor();
    assert.equal(
      await page.locator(".serial-feedback:not([hidden])").count(),
      0,
    );
    assert.equal(await serials.nth(0).inputValue(), "REPEATED\nREPEATED");
    await serials.nth(0).fill("0001\n\n0002\n");
    assert.equal(
      await page.locator(".serial-feedback:not([hidden])").count(),
      0,
    );
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    await page.locator("#generated-table").waitFor();
    assert.equal(
      await page.locator(".serial-input").first().inputValue(),
      "0001\n\n0002\n",
    );
    await page.locator('[name="notes_1"]').fill("Checked");
    assert.equal(
      await page
        .getByRole("button", { name: "Save work order", exact: true })
        .isDisabled(),
      true,
    );
    assert.equal(await page.locator("#generated-table").isVisible(), false);
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    await page.locator("#generated-table").waitFor();
    await page
      .getByRole("button", { name: "Save work order", exact: true })
      .click();
    await page.waitForURL("**/generated/wo/*");
    const savedUrl = page.url();
    await page
      .getByRole("link", { name: "Revise work order", exact: true })
      .click();
    await page.locator('[name="notes_1"]').fill("Revised");
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Save revision", exact: true })
      .click();
    await page.waitForURL(savedUrl);
    assert.match(await page.locator("#word-picking").textContent(), /Revised/);
    await go("/wo/new");
    await page.getByLabel("NTA Order ID", { exact: true }).fill("MANUAL-UI");
    await page.getByLabel("Customer", { exact: true }).fill("UI fixture");
    await page.getByLabel("Item", { exact: true }).fill("Manual product");
    await page.getByLabel("Qty", { exact: true }).fill("2");
    await page.getByLabel("Serial numbers", { exact: true }).fill("NA\nNA");
    await page.getByRole("button", { name: "Add item", exact: true }).click();
    assert.equal(await page.locator("#manual-items tr").count(), 2);
    await page
      .getByRole("button", { name: "Remove item", exact: true })
      .last()
      .click();
    assert.equal(await page.locator("#manual-items tr").count(), 1);
    await page
      .getByRole("button", { name: "Preview work order", exact: true })
      .click();
    await page.locator("#generated-table").waitFor();
    await page
      .getByRole("button", { name: "Save work order", exact: true })
      .click();
    await page.waitForURL("**/generated/wo/*");
    await go("/production_planning/orders/SO-20261209");
    const initialOrder = await page
      .locator("#approved-item-order")
      .inputValue();
    await page.locator(".approval-down").first().click();
    assert.notEqual(
      await page.locator("#approved-item-order").inputValue(),
      initialOrder,
    );
    await page
      .getByRole("button", { name: "Approve sales order", exact: true })
      .click();
    await page.waitForURL("**/production_planning/orders");
    await go("/production_planning");
    const row = page.locator('.order-line[data-wo="SO-20261208"]');
    await row.getByRole("button", { name: "Move to FG", exact: true }).click();
    assert.equal(
      await page
        .getByRole("button", { name: "Save Schedule", exact: true })
        .isEnabled(),
      true,
    );
    const scheduled = page.waitForResponse((response) =>
      response.url().endsWith("/api/production_schedule"),
    );
    await page
      .getByRole("button", { name: "Save Schedule", exact: true })
      .click();
    assert.equal((await scheduled).status(), 200);
    await page.waitForLoadState("networkidle");
    await page
      .locator(
        '[data-target-area="finished_goods"] .order-line[data-wo="SO-20261208"]',
      )
      .waitFor();
    const report = {
      checks,
      errors,
      externalRequests: requests,
      workflowChecks: "passed",
    };
    fs.writeFileSync(`${output}/report.json`, JSON.stringify(report, null, 2));
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    const violations = checks.filter((check) => check.accessibility.length);
    assert.deepEqual(
      violations,
      [],
      "Accessibility violations: inspect report.json",
    );
    console.log(
      "Passed: 45 responsive page checks, 18 accessibility checks, real preview/save/revision/manual/approval/schedule flows; no JS errors or external requests.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
