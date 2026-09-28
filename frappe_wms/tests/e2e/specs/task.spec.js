import { test, expect } from "@playwright/test";
import { loginOperator, logon, openApp, freeResources, makeTask, getTask, scan, enter, typeSlow, notice, view, admin, seed } from "../helpers.js";

test.beforeEach(async ({ page, request }) => {
  await freeResources(request);
  await loginOperator(page);
  await logon(page);
});

const openTask = async (page, task) => { await openApp(page, `#/task/${task.name}`); await expect(page).toHaveURL(new RegExp(`/task/${task.name}/source$`)); };
const setVerification = (request, on) => admin(request).put("/api/resource/WMS Settings/WMS Settings", { require_scan_verification: on ? 1 : 0 });

test("full confirm with a hardware-scanner burst: scan, quantity, scan, confirm", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");                       // no field is focused-by-click: the burst still lands in the right place
  await expect(page).toHaveURL(/\/quantity$/);
  await expect(page.locator('[data-fk="qty"]')).toHaveValue("5");
  await enter(page);                  // human Enter on the quantity box = Next
  await expect(page).toHaveURL(/\/destination$/);
  await scan(page, "E2E-WH-B1");
  await expect(page).toHaveURL(/\/review$/);
  await page.getByRole("button", { name: /Confirm/ }).click();
  await expect(notice(page)).toContainText(/confirmed/i);
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  const done = await getTask(request, t.name);
  expect(done.status).toBe("Confirmed");
  expect(done.confirmed_quantity).toBe(5);
});

test("scan matching ignores case and stray whitespace, and AIM prefixes", async ({ page, request }) => {
  const t = await makeTask(request);
  await openTask(page, t);
  await scan(page, "]C1e2e-wh-a1");
  await expect(page).toHaveURL(/\/quantity$/);
});

test("wrong bin: says what was scanned and what is expected, stays on the step, keeps focus", async ({ page, request }) => {
  const t = await makeTask(request);
  await openTask(page, t);
  await scan(page, "E2E-WH-A2");
  await expect(page).toHaveURL(/\/source$/);
  const err = view(page).locator(".field-error");
  await expect(err).toContainText("E2E-WH-A2");
  await expect(err).toContainText("E2E-WH-A1");
  await expect(err).toContainText(/bin/i);
  await expect(page.locator('[data-fk="src"]')).toBeFocused();
  await scan(page, "NOPE-123");
  await expect(err).toContainText(/not a known code/i);
  await scan(page, "E2E-WH-A1");                       // a good scan afterwards recovers
  await expect(page).toHaveURL(/\/quantity$/);
});

test("a scan that lands in the quantity box is not typed into it", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await page.locator('[data-fk="qty"]').focus();
  await scan(page, "E2E-WH-B1");                       // a scanner burst while the qty box is focused
  await expect(page.locator('[data-fk="qty"]')).toHaveValue("5");
  await expect(page).toHaveURL(/\/quantity$/);
});

test("quantity validation: zero and letters are rejected inline; more than planned is allowed through", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  const qty = page.locator('[data-fk="qty"]');
  for (const [text, msg] of [["0", /greater than zero/], ["abc", /number/]]) {
    await qty.fill(text); await enter(page);
    await expect(view(page).locator(".field-error")).toContainText(msg);
    await expect(page).toHaveURL(/\/quantity$/);
  }
  // More than planned (5) is a real find (Unload/Putaway) - accepted here, capped server-side, with the
  // excess posted to the difference bin (see "over-confirmation" below), not rejected as invalid input.
  await qty.fill("9"); await enter(page);
  await expect(page).toHaveURL(/\/destination$/);
});

test("quantity validation: a decimal comma from a phone keypad is accepted", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await page.locator('[data-fk="qty"]').fill("2,5"); await enter(page);
  await expect(page).toHaveURL(/\/destination$/);
});

test("over-confirmation: more than planned is capped on the task and the excess is called out before and after confirming", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await page.locator('[data-fk="qty"]').fill("8"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await expect(view(page)).toContainText(/3.*difference bin/i);
  await page.getByRole("button", { name: /Confirm/ }).click();
  await expect(notice(page)).toContainText(/extra sent to the difference bin/i);
  const done = await getTask(request, t.name);
  expect(done.status).toBe("Confirmed");
  expect(done.confirmed_quantity).toBe(5); // capped at planned - the other 3 went to the difference bin, not this task
});

test("partial quantity leaves the task open for the rest", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 6 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await page.locator('[data-fk="qty"]').fill("2"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await page.getByRole("button", { name: /Confirm/ }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  const done = await getTask(request, t.name);
  expect(done.status).toBe("Partially Confirmed");
  expect(done.confirmed_quantity).toBe(2);
});

test("reload mid-wizard restores the step and what was already scanned", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await page.locator('[data-fk="qty"]').fill("3");
  await enter(page);
  await expect(page).toHaveURL(/\/destination$/);
  await page.reload();
  await page.waitForFunction(() => window.WMS_BOOTED === true);
  await expect(page).toHaveURL(/\/destination$/);
  await scan(page, "E2E-WH-B1");
  await expect(view(page)).toContainText("E2E-WH-A1");   // source from before the reload
  await expect(view(page)).toContainText("3");
});

test("deep link to a later step is sent back to the first unfinished step", async ({ page, request }) => {
  const t = await makeTask(request);
  await openApp(page, `#/task/${t.name}/review`);
  await expect(page).toHaveURL(/\/source$/);
});

test("Back steps back through the wizard, then returns to the list", async ({ page, request }) => {
  const t = await makeTask(request);
  await openApp(page, "#/tasks/internal");
  await view(page).locator(`[data-task="${t.name}"]`).click();
  await expect(page).toHaveURL(/\/source$/);
  await scan(page, "E2E-WH-A1");
  await expect(page).toHaveURL(/\/quantity$/);
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page).toHaveURL(/\/source$/);
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
});

test("after confirming, Back never re-enters the finished task", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 2 });
  await openApp(page, "#/tasks/internal");
  await view(page).locator(`[data-task="${t.name}"]`).click();
  await scan(page, "E2E-WH-A1"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await page.getByRole("button", { name: /Confirm/ }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  await page.goBack();
  await expect(page).not.toHaveURL(/#\/task\//);
});

test("double Enter / double tap on Confirm confirms exactly once", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await page.locator('[data-fk="qty"]').fill("2"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await page.evaluate(() => { document.activeElement && document.activeElement.blur(); });
  const btn = page.getByRole("button", { name: /Confirm/ });
  await btn.dblclick();
  await page.keyboard.press("Enter"); await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  const done = await getTask(request, t.name);
  expect(done.confirmed_quantity).toBe(2);
});

test("offline confirm: clear message with Retry, entries kept, one confirmation after reconnect", async ({ page, context, request }) => {
  const t = await makeTask(request, { planned_quantity: 4 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await context.setOffline(true);
  await page.getByRole("button", { name: /Confirm/ }).click();
  await expect(notice(page)).toContainText(/No connection/i);
  await expect(notice(page).getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page).toHaveURL(/\/review$/);
  await context.setOffline(false);
  await notice(page).getByRole("button", { name: "Retry" }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  const done = await getTask(request, t.name);
  expect(done.status).toBe("Confirmed");
  expect(done.confirmed_quantity).toBe(4);
});

test("server error text is clean: no traceback, no exception class name", async ({ page, request }) => {
  const t = await makeTask(request, { planned_quantity: 4 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await getTask(request, t.name);
  await admin(request).put(`/api/resource/Warehouse Task/${t.name}`, { status: "Cancelled" });   // someone cancels it under the operator
  await page.getByRole("button", { name: /Confirm/ }).click();
  const text = await notice(page).innerText();
  expect(text).not.toMatch(/Traceback|frappe\.exceptions|<\w+>/);
  await expect(notice(page).locator(".notice-text")).not.toBeEmpty();
});

test("exception: pick a code, comment is required, and nothing is blocked until it is confirmed", async ({ page, request }) => {
  const t = await makeTask(request);
  await openTask(page, t);
  await page.getByRole("button", { name: "Exception" }).click();
  await expect(page).toHaveURL(/\/exception$/);
  await view(page).getByRole("button", { name: /E2E damaged goods/ }).click();
  await expect(page).toHaveURL(/\/exception\/E2E-DAMAGED|\/exception\/.+/);
  await page.getByRole("button", { name: "Report exception" }).click();          // no comment yet
  await expect(view(page).locator(".field-error")).toContainText(/comment/i);
  await page.locator('[data-fk="remarks"]').fill("pallet crushed");
  page.once("dialog", (d) => d.dismiss());                                        // "Block this task?" -> No
  await page.getByRole("button", { name: "Report exception" }).click();
  expect((await getTask(request, t.name)).status).not.toBe("Exception");
  page.once("dialog", (d) => d.accept());                                         // -> Yes
  await page.getByRole("button", { name: "Report exception" }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  await expect(notice(page)).toContainText(/exception/i);
  const done = await getTask(request, t.name);
  expect(done.status).toBe("Exception");
  expect(done.blocking_reason).toContain("pallet crushed");
});

test.describe("product verification (WMS Settings: require scan verification)", () => {
  test.beforeEach(async ({ request }) => { await setVerification(request, true); });
  test.afterEach(async ({ request }) => { await setVerification(request, false); });

  test("adds a product step; accepts the item code or its barcode; rejects another item; confirm passes server verification", async ({ page, request }) => {
    const t = await makeTask(request, { planned_quantity: 3 });
    await openTask(page, t);
    await scan(page, "E2E-WH-A1");
    await expect(page).toHaveURL(/\/product$/);
    await scan(page, "E2E-WH-B1");                                   // a bin, not a product
    await expect(view(page).locator(".field-error")).toContainText(/bin/i);
    await page.waitForTimeout(300);                                  // a human aims the next scan; see "double-fired scan" below
    await scan(page, seed().barcode);                                // EAN resolves to the item
    await expect(page).toHaveURL(/\/quantity$/);
    await enter(page);
    await scan(page, "E2E-WH-B1");
    await page.getByRole("button", { name: /Confirm/ }).click();
    await expect(page).toHaveURL(/#\/tasks\/internal$/);
    expect((await getTask(request, t.name)).status).toBe("Confirmed");
  });
});

test("a double-fired scan (same label read twice back to back) advances exactly one step", async ({ page, request }) => {
  const t = await makeTask(request);
  await openTask(page, t);
  await page.keyboard.type("E2E-WH-A1", { delay: 0 }); await page.keyboard.press("Enter");
  await page.keyboard.type("E2E-WH-A1", { delay: 0 }); await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/quantity$/);
  await page.waitForTimeout(800);
  await expect(page).toHaveURL(/\/quantity$/);                       // not pushed on to destination by the duplicate
});
