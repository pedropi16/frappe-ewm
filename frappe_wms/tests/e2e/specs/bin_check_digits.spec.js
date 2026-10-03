import { test, expect } from "@playwright/test";
import { loginOperator, logon, openApp, freeResources, scan, enter, notice, view, admin, seed, makeTask, getTask } from "../helpers.js";

const openTask = async (page, task) => { await openApp(page, `#/task/${task.name}`); await expect(page).toHaveURL(new RegExp(`/task/${task.name}/source$`)); };
const setCheckDigits = (request, on) => admin(request).put("/api/resource/WMS Settings/WMS Settings", { require_bin_check_digits: on ? 1 : 0 });

test.describe("bin check digits (WMS Settings: require bin check digits)", () => {
  test.beforeEach(async ({ page, request }) => {
    await setCheckDigits(request, true);
    await freeResources(request);
    await loginOperator(page);
    await logon(page);
  });
  test.afterEach(async ({ request }) => { await setCheckDigits(request, false); });

  test("a pure-bin task asks for check digits instead of a bin scan; wrong digits are rejected, correct ones confirm", async ({ page, request }) => {
    const t = await makeTask(request, { planned_quantity: 2 });
    const srcDigits = (await admin(request).get("/api/resource/Storage Bin/E2E-WH-A1")).data.check_digits;
    const dstDigits = (await admin(request).get("/api/resource/Storage Bin/E2E-WH-B1")).data.check_digits;
    await openTask(page, t);
    await expect(view(page)).toContainText(/check digit/i);
    await page.locator('[data-fk="src"]').fill("ZZ");
    await enter(page);
    await expect(view(page).locator(".field-error")).toContainText(/don't match/i);
    await page.locator('[data-fk="src"]').fill(srcDigits);
    await enter(page);
    await expect(page).toHaveURL(/\/quantity$/);
    await enter(page);
    await expect(page).toHaveURL(/\/destination$/);
    await expect(view(page)).toContainText(/check digit/i);
    await page.locator('[data-fk="dst"]').fill(dstDigits);
    await enter(page);
    await expect(page).toHaveURL(/\/review$/);
    await page.getByRole("button", { name: /Confirm/ }).click();
    await expect(notice(page)).toContainText(/confirmed/i);
    expect((await getTask(request, t.name)).status).toBe("Confirmed");
  });
  // A task with both a bin and an HU on the same side keeps the ordinary scan step (out of
  // scope for v1 - see services/task.py's _check_digits_bin) - covered at the integration level
  // in tests/test_bin_check_digits.py, which doesn't need a real fixture HU to prove it.

  test("ad-hoc Internal Move also asks for check digits, not just the planned-task wizard", async ({ page, request }) => {
    // The gap this session's audit found: move.js's own scan-to-bin fields bypassed check digits
    // entirely, even though task.js's generic wizard already enforced them - closed by having
    // move.js ask the same question once a scanned bin resolves and has no competing HU.
    const s = seed();
    const srcDigits = (await admin(request).get("/api/resource/Storage Bin/E2E-WH-A1")).data.check_digits;
    const dstDigits = (await admin(request).get("/api/resource/Storage Bin/E2E-WH-B1")).data.check_digits;
    await openApp(page, "#/move-manual");
    await scan(page, s.barcode);
    await page.locator('[data-fk="quantity"]').fill("1");
    await enter(page);
    await expect(page).toHaveURL(/\/source$/);
    await scan(page, "E2E-WH-A1");
    await expect(view(page)).toContainText(/check digit/i);
    await page.locator('[data-fk="source_check"]').fill("ZZ");
    await enter(page);
    await expect(view(page).locator(".field-error")).toContainText(/don't match/i);
    await page.locator('[data-fk="source_check"]').fill(srcDigits);
    await enter(page);
    await expect(page).toHaveURL(/\/destination$/);
    await scan(page, "E2E-WH-B1");
    await expect(view(page)).toContainText(/check digit/i);
    await page.locator('[data-fk="destination_check"]').fill(dstDigits);
    await enter(page);
    await expect(page).toHaveURL(/\/review$/);
    await page.getByRole("button", { name: /Move$/ }).click();
    await expect(notice(page)).toContainText(/Moved 1/);
  });
});
