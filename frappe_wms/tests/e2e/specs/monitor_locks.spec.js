import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin, loginOperator } from "../helpers.js";

// Display / change mode: the delivery screen opens for display; Change locks it, a second user can only display it until the first goes back to display.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let demo;
test.beforeAll(() => { bench("cleanup_outbound"); demo = JSON.parse(bench("outbound_demo").split("\n").find((l) => l.startsWith("E2E_OUTBOUND ")).slice(13)); });
test.afterAll(() => bench("cleanup_outbound"));

test("a delivery changed by one user is display-only for another", async ({ page, browser }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto(`/app/wms-outbound-delivery/${demo.delivery}`);
  await expect(page.getByRole("button", { name: "Allocate Stock" })).toHaveCount(0);   // display mode: no actions
  await page.getByRole("button", { name: "Change", exact: true }).click();
  await expect(page.getByRole("button", { name: "Display", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Allocate Stock|Create Pick Tasks/ }).first()).toBeVisible();

  const other = await browser.newContext({ baseURL: page.url().split("/app")[0] });
  const op = await other.newPage();
  await loginOperator(op);
  await op.goto(`/app/wms-outbound-delivery/${demo.delivery}`);
  await expect(op.getByRole("button", { name: /Display only: being changed by/ })).toBeVisible();
  await expect(op.getByRole("button", { name: /Allocate Stock|Create Pick Tasks/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Display", exact: true }).click();
  await op.getByRole("button", { name: /Display only/ }).click();   // reloads the screen: free again
  await expect(op.getByRole("button", { name: "Change", exact: true })).toBeVisible();
  await other.close();
  expect(errors).toEqual([]);
});

test("a document form opened for change is display-only for the next user", async ({ page, browser, request }) => {
  const s = seed();
  const doc = (await admin(request).post("/api/resource/WMS Stock Adjustment", { warehouse: s.warehouse, adjustment_type: "Unplanned Receipt", product: s.item, storage_bin: s.bins[0], quantity: 1, reason: "lock e2e" })).data;
  try {
    expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
    await page.goto(`/app/wms-stock-adjustment/${doc.name}`);
    await expect(page.getByRole("button", { name: "Post", exact: true })).toBeVisible();
    await expect.poll(async () => (await admin(request).method("frappe_wms.api.locks.list_locks", {})).some((l) => l.object_name === doc.name)).toBeTruthy();
    const other = await browser.newContext({ baseURL: page.url().split("/app")[0] });
    const op = await other.newPage();
    await loginOperator(op);
    await op.goto(`/app/wms-stock-adjustment/${doc.name}`);
    await expect(op.locator(".form-message, .form-dashboard-section", { hasText: "Display only: being changed by" }).first()).toBeVisible();
    await other.close();
  } finally { bench("cleanup_adjustments"); }
});
