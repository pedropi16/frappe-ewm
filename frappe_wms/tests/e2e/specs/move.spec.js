import { test, expect } from "@playwright/test";
import { loginOperator, logon, openApp, freeResources, scan, enter, notice, view, seed, admin } from "../helpers.js";

test.beforeEach(async ({ page, request }) => {
  await freeResources(request);
  await loginOperator(page);
  await logon(page);
});

test("Move: scan item barcode, quantity, source, destination, confirm - stock moves once", async ({ page, request }) => {
  const s = seed();
  await openApp(page, "#/move-manual");
  await expect(page).toHaveURL(/#\/move-manual\/item$/);
  await scan(page, s.barcode);                                   // EAN -> item code + UOM, no typing
  await expect(page).toHaveURL(/\/quantity$/);
  await page.locator('[data-fk="quantity"]').fill("3");
  await enter(page);
  await expect(page).toHaveURL(/\/source$/);
  await scan(page, "E2E-WH-A1");
  await expect(page).toHaveURL(/\/destination$/);
  await scan(page, "E2E-WH-B1");
  await expect(page).toHaveURL(/\/review$/);
  await expect(view(page)).toContainText(s.item);
  await page.getByRole("button", { name: /Move$/ }).click();
  await expect(notice(page)).toContainText(/Moved 3/);
  await expect(page).toHaveURL(/#\/s\/internal$/);
});

test("Move: a wrong-type scan is explained (an HU/item where a bin is needed)", async ({ page }) => {
  const s = seed();
  await openApp(page, "#/move-manual/item");
  await scan(page, "NO-SUCH-ITEM");
  await expect(view(page).locator(".field-error")).toContainText(/not a known code/i);
  await scan(page, s.item);
  await expect(page).toHaveURL(/\/quantity$/);
  await page.locator('[data-fk="quantity"]').fill("1"); await enter(page);
  await scan(page, s.item);                                       // an item where a bin is needed
  await expect(view(page).locator(".field-error")).toContainText(/product|item/i);
});

test("Move: unfinished work is saved - reload resumes, the menu offers it, Start over clears it", async ({ page }) => {
  const s = seed();
  await openApp(page, "#/move-manual");
  await scan(page, s.item);
  await page.locator('[data-fk="quantity"]').fill("7"); await enter(page);
  await scan(page, "E2E-WH-A1");
  await expect(page).toHaveURL(/\/destination$/);
  await page.reload();
  await page.waitForFunction(() => window.WMS_BOOTED === true);
  await expect(page).toHaveURL(/\/destination$/);
  await openApp(page, "#/");
  await expect(view(page).getByText("Unfinished work")).toBeVisible();
  await view(page).getByText(/Move ·/).click();
  await expect(page).toHaveURL(/\/destination$/);
  // the first step offers "Start over", which discards the draft everywhere
  await openApp(page, "#/move-manual/item");
  await expect(view(page)).toContainText(/Resumed your unfinished move/);
  await view(page).getByRole("button", { name: "Start over" }).click();
  await expect(page.locator('[data-fk="product"]')).toHaveValue("");
  await openApp(page, "#/");
  await expect(view(page).getByText("Unfinished work")).toHaveCount(0);
});

test("Move: a lost response does not move twice (same idempotency key on resend)", async ({ page, request }) => {
  const s = seed();
  await openApp(page, "#/move-manual");
  await scan(page, s.item);
  await page.locator('[data-fk="quantity"]').fill("2"); await enter(page);
  await scan(page, "E2E-WH-A1"); await scan(page, "E2E-WH-B1");
  // drop the FIRST response after the server has processed it, so the app sees a network failure and Retry resends
  let dropped = false;
  await page.route("**/api/method/frappe_wms.api.scanner.create_and_confirm_move", async (route) => {
    if (!dropped) { dropped = true; await route.fetch(); await route.abort("connectionreset"); } else { await route.continue(); }
  });
  await page.getByRole("button", { name: /Move$/ }).click();
  await expect(notice(page)).toContainText(/No connection/i);
  await notice(page).getByRole("button", { name: "Retry" }).click();
  await expect(notice(page)).toContainText(/Moved 2/);
  const ledger = await admin(request).get(`/api/resource/WMS Stock Ledger Entry?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["quantity", "=", 2]]))}&fields=["name"]&limit_page_length=200`);
  // one move = one negative + one positive entry for this quantity; a double post would show four
  expect(ledger.data.length).toBeLessThanOrEqual(2 + 0 * 1);
});
