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

test("a stale CSRF token (session cookie rotated mid-shift) is refreshed and the confirm resent automatically", async ({ page, request }) => {
  // Reproduced live against production: the session cookie can rotate under the operator
  // (Frappe's own renewal) within seconds of a fresh page load, silently invalidating the CSRF
  // token core/api.js embedded at boot - every mutating call then failed with a raw "Invalid
  // Request" (CSRFTokenError), forever, since the operator IS still logged in and sessionIsDead()
  // never catches it. Simulated here by corrupting the token directly rather than depending on a
  // real server-side rotation (not reliably triggerable on demand) - the fix's own recovery path
  // (refetch this page's HTML, pull the fresh token out, resend the exact same call once) doesn't
  // care how the token went stale.
  const t = await makeTask(request, { planned_quantity: 3 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1"); await enter(page);
  await scan(page, "E2E-WH-B1");
  await page.evaluate(() => { window.WMS.csrf = "deliberately-stale-token"; });
  await page.getByRole("button", { name: /Confirm/ }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/, { timeout: 10000 });
  await expect(notice(page)).not.toContainText(/Invalid Request|CSRFTokenError/i);
  const done = await getTask(request, t.name);
  expect(done.status).toBe("Confirmed");
  expect(done.confirmed_quantity).toBe(3);
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

test("short pick: the quantity already typed carries straight into the exception's quantity-found field", async ({ page, request }) => {
  // The SAP EWM comparison's "cheap, do it" gap: a short pick used to need picking a code first
  // and then retyping the quantity on its own screen - this carries whatever was already typed
  // on the quantity step through, so the common case is one fewer field to fill in.
  const t = await makeTask(request, { planned_quantity: 5 });
  await openTask(page, t);
  await scan(page, "E2E-WH-A1");
  await expect(page).toHaveURL(/\/quantity$/);
  await page.locator('[data-fk="qty"]').fill("2");
  await page.getByRole("button", { name: "Can't find it all - report short" }).click();
  await expect(page).toHaveURL(/\/exception$/);
  await expect(view(page)).toContainText(/Carrying over the 2/);
  await view(page).getByRole("button", { name: /Damaged Product Found/ }).click();
  await expect(page).toHaveURL(/\/exception\/DAMAGED$/);
  await expect(page.locator('[data-fk="revised"]')).toHaveValue("2");
  await page.locator('[data-fk="remarks"]').fill("found 2 good, rest damaged");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Report exception" }).click();
  await expect(page).toHaveURL(/#\/tasks\/internal$/);
  expect((await getTask(request, t.name)).status).toBe("Exception");
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

test("counting unit: the quantity is typed in cases and confirmed in stock units", async ({ page, request }) => {
  const s = seed(), a = admin(request);
  try { await a.get("/api/resource/UOM/E2E Case"); } catch (e) { await a.post("/api/resource/UOM", { uom_name: "E2E Case" }); }
  const item = (await a.get(`/api/resource/Item/${encodeURIComponent(s.item)}`)).data;
  const before = item.uoms.map((u) => ({ uom: u.uom, conversion_factor: u.conversion_factor }));
  await a.put(`/api/resource/Item/${encodeURIComponent(s.item)}`, { uoms: [...before.filter((u) => u.uom !== "E2E Case"), { uom: "E2E Case", conversion_factor: 4 }] });
  try {
    const t = await makeTask(request, { planned_quantity: 8 });
    await openTask(page, t);
    await scan(page, "E2E-WH-A1");
    await expect(page).toHaveURL(/\/quantity$/);
    await page.locator('[data-fk="uom"]').selectOption("E2E Case");
    await expect(page.locator('[data-fk="qty"]')).toHaveValue("2");          // 8 stock units shown as 2 cases
    await page.locator('[data-fk="qty"]').fill("1");
    await expect(view(page)).toContainText(`= 4 ${s.uom}`);
    await enter(page);
    await scan(page, "E2E-WH-B1");
    await expect(view(page)).toContainText("(1 E2E Case)");
    await page.getByRole("button", { name: /Confirm/ }).click();
    await expect(page).toHaveURL(/#\/tasks\/internal$/);
    const done = await getTask(request, t.name);
    expect(done.confirmed_quantity).toBe(4);
    expect(done.status).toBe("Partially Confirmed");
  } finally {
    await a.put(`/api/resource/Item/${encodeURIComponent(s.item)}`, { uoms: before });
  }
});
