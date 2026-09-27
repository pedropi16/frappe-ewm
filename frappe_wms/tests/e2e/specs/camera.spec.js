import { test, expect } from "@playwright/test";
import { loginOperator, logon, openApp, freeResources, makeTask, notice, view } from "../helpers.js";

// Runs in the "camera" project: Chromium's fake camera plays a QR code for E2E-WH-A1 (see make-camera-fixture.js).
// Headless Chromium has no BarcodeDetector, so this exercises the vendored-ZXing path - the same one iOS Safari takes.
test.beforeEach(async ({ page, request }) => {
  await freeResources(request);
  await loginOperator(page);
  await logon(page);
});

test("camera button scans a barcode into the field and advances the step", async ({ page, request, context }) => {
  await context.grantPermissions(["camera"]);
  const t = await makeTask(request);
  await openApp(page, `#/task/${t.name}`);
  await expect(page).toHaveURL(/\/source$/);
  await page.getByRole("button", { name: "Scan with camera" }).click();
  await expect(page.locator(".cam-overlay")).toBeVisible();
  await expect(page).toHaveURL(/\/quantity$/, { timeout: 15000 });   // decoded E2E-WH-A1 == the expected source bin
  await expect(page.locator(".cam-overlay")).toHaveCount(0);
});

test("closing the camera leaves the field untouched", async ({ page, request, context }) => {
  await context.grantPermissions(["camera"]);
  const t = await makeTask(request);
  await openApp(page, `#/task/${t.name}`);
  await page.getByRole("button", { name: "Scan with camera" }).click();
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.locator(".cam-overlay")).toHaveCount(0);
  await expect(page).toHaveURL(/\/source$/);
});
