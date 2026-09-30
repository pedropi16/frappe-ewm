import { test, expect } from "@playwright/test";
import { loginOperator, logon, openApp, freeResources, scan, enter, notice, view, seed, admin } from "../helpers.js";

test.beforeEach(async ({ page, request }) => {
  await freeResources(request);
  await loginOperator(page);
  await logon(page);
});

test("Lookup: one scan field, type is detected (no dropdown), shows stock", async ({ page }) => {
  await openApp(page, "#/lookup");
  await scan(page, "E2E-WH-A1");
  await expect(view(page)).toContainText("Storage Bin E2E-WH-A1");
  await scan(page, "NOPE-999");
  await expect(view(page).locator(".field-error")).toContainText(/Nothing found/i);
});

test("Handling Units: create with a blank barcode, open it, back to the list", async ({ page }) => {
  await openApp(page, "#/hu");
  await page.getByRole("button", { name: /Create Handling Unit/ }).click();
  await expect(page).toHaveURL(/#\/hu-new$/);
  await page.locator('[data-fk="type"]').selectOption("E2E-PALLET");
  const code = `E2EHU${Date.now().toString().slice(-7)}`;
  await scan(page, code);
  await scan(page, "E2E-WH-A1");
  await page.getByRole("button", { name: /Create$/ }).click();
  await expect(notice(page)).toContainText(/created/i);
  await expect(page).toHaveURL(/#\/hu$/);
  await view(page).getByText(code).click();
  await expect(page).toHaveURL(new RegExp(`#/hu/${code}$`));
  await expect(view(page)).toContainText("E2E-WH-A1");
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page).toHaveURL(/#\/hu$/);
});

test("Handling Unit form validates before sending", async ({ page }) => {
  await openApp(page, "#/hu-new");
  await page.getByRole("button", { name: /Create$/ }).click();
  await expect(view(page).locator(".field-error").first()).toBeVisible();
  await expect(page).toHaveURL(/#\/hu-new$/);
});

test("Device & session: preferences persist across reload; log off returns to logon", async ({ page }) => {
  await openApp(page, "#/session");
  await page.getByRole("button", { name: /Sound: On/ }).click();
  await expect(page.getByRole("button", { name: /Sound: Off/ })).toBeVisible();
  await page.reload(); await page.waitForFunction(() => window.WMS_BOOTED === true);
  await expect(page.getByRole("button", { name: /Sound: Off/ })).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Log off this device" }).click();
  await expect(page).toHaveURL(/#\/logon$/);
});

test("session expiry: the operator is sent to login, not shown a bare error", async ({ page, context }) => {
  await openApp(page, "#/tasks/internal");
  await context.clearCookies();
  await page.getByRole("button", { name: "Refresh" }).click();
  await page.waitForURL(/\/login/);
  expect(page.url()).toContain("redirect-to=");
});

test("every screen renders without a JS error (smoke over all routes)", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  const routes = ["", "s/inbound", "s/internal", "s/outbound", "lookup", "session", "tasks/inbound", "tasks/internal", "tasks/outbound", "receive", "ship", "pack", "count", "quality", "load",
    "decon", "close-movement", "repack", "hu", "hu-new", "kitting", "consolidation", "picking", "picking-manual", "picking-find/hu", "vas", "vas-new", "move"];
  for (const r of routes) {
    await openApp(page, `#/${r}`);
    await page.waitForTimeout(350);
    await expect(page.locator("#view")).not.toContainText("could not be displayed");
    await expect(page.locator("#view"), `#/${r} renders a bare null/undefined`).not.toContainText(/\b(null|undefined)\b/);
  }
  expect(errors, errors.join("\n")).toEqual([]);
});
