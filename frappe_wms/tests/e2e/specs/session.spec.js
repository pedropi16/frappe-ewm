import { test, expect } from "@playwright/test";
import { loginOperator, openApp, freeResources, logon, view, notice } from "../helpers.js";

test.beforeEach(async ({ request }) => { await freeResources(request); });

test("logon lists free devices, picking one lands on the menu, reload stays put", async ({ page }) => {
  await loginOperator(page);
  await openApp(page);
  await expect(page).toHaveURL(/#\/logon$/);
  await expect(view(page).getByRole("button", { name: /E2E-RF1/ })).toBeVisible();
  await view(page).getByRole("button", { name: /E2E-RF1/ }).click();
  await expect(page).toHaveURL(/#\/$/);
  await expect(view(page).getByRole("button", { name: /Inbound/ })).toBeVisible();
  await expect(notice(page)).toContainText("Logged on to E2E-RF1");

  await page.reload();
  await page.waitForFunction(() => window.WMS_BOOTED === true);
  await expect(view(page).getByRole("button", { name: /Inbound/ })).toBeVisible();
  await expect(page).not.toHaveURL(/logon/);
});

test("reload on a deep screen returns to that screen; unknown hash falls back to the menu", async ({ page }) => {
  await loginOperator(page);
  await logon(page);
  await openApp(page, "#/s/internal");
  await expect(view(page).getByRole("button", { name: /Count/ })).toBeVisible();
  await page.reload();
  await page.waitForFunction(() => window.WMS_BOOTED === true);
  await expect(page).toHaveURL(/#\/s\/internal$/);
  await openApp(page, "#/nonsense/route");
  await expect(view(page).getByRole("button", { name: /Inbound/ })).toBeVisible();
});

test("Back walks the path taken and never leaves the app from a deep link", async ({ page }) => {
  await loginOperator(page);
  await logon(page);
  await openApp(page);
  await view(page).getByRole("button", { name: /Internal/ }).click();
  await expect(page).toHaveURL(/#\/s\/internal$/);
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page).toHaveURL(/#\/$/);
  await view(page).getByRole("button", { name: /Lookup/ }).click();
  await expect(page).toHaveURL(/#\/lookup$/);
  await page.goBack(); // the phone's own Back gesture
  await expect(page).toHaveURL(/#\/$/);
  // deep link at history depth 0: the in-app Back falls back to the parent instead of exiting
  await openApp(page, "#/s/outbound");
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page).toHaveURL(/#\/$/);
  expect(page.url()).toContain("/wms");
});
