import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Ad Hoc Processing: the advanced selection finds many stock lines, one action creates the tasks for all marked ones.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); bench("adhoc_stock"); });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adhoc"); });

test("ad hoc tasks for several marked stock lines", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-adhoc/adprod");
  await page.locator(".wms-ah-wh").selectOption(s.warehouse);
  await page.locator(".modal.show", { hasText: "Selection - Stock Overview" }).getByRole("button", { name: /Execute/ }).click();
  const row = (sn) => page.locator(".wms-ah-res tbody tr", { hasText: sn }).first();
  await row("WAREHOUSE_BLOCKED").locator("th.wms-grid-rowhead").click();
  await row("AVAILABLE").locator("th.wms-grid-rowhead").click({ modifiers: ["Control"] });
  await expect(page.locator(".wms-grid-selcount")).toContainText("2 selected");
  await page.locator(".wms-grid-actionbar").getByRole("button", { name: /Create Tasks/ }).click();
  await page.locator(".modal.show input[data-fieldname='destination_bin']").fill(s.bins[1]);
  await expect(page.locator(".modal.show input[data-fieldname='process_type']")).toBeVisible();
  await expect(page.locator(".modal.show input[data-fieldname='confirm']")).toBeVisible();
  await page.locator(".modal.show textarea[data-fieldname='reason']").fill("e2e re-slotting");
  await page.locator(".modal.show .btn-primary", { hasText: "Create Tasks" }).click();
  await expect.poll(async () => (await admin(request).get(`/api/resource/Warehouse Task?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["stock_type_from", "in", ["WAREHOUSE_BLOCKED", "AVAILABLE"]], ["destination_bin", "=", s.bins[1]], ["reason", "=", "e2e re-slotting"]]))}&fields=["name"]`)).data.length).toBe(2);
  expect(errors).toEqual([]);
});

test("every ad hoc transaction opens its selection without a script error", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  for (const [tx, title] of [["adhu", "Handling Units"], ["posting", "Stock Overview"], ["scrap", "Stock Overview"], ["hublock", "Handling Units"], ["tasks", "Warehouse Tasks"], ["wo", "Warehouse Orders"], ["wave", "Waves"]]) {
    await page.goto(`/app/wms-adhoc/${tx}`);
    await expect(page.locator(".modal.show", { hasText: `Selection - ${title}` })).toBeVisible();
  }
  expect(errors).toEqual([]);
});
