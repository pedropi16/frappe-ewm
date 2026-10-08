import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Stock Overview only shows stock; its shortcuts open the adjustment documents prefilled, and the documents post.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adjustments"); });

test("stock overview opens the adjustment documents: unplanned stock, then a posting change", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Stock Overview" }).click();
  await page.locator(".modal.show", { hasText: "Selection - Stock Overview" }).getByRole("button", { name: /Execute/ }).click();

  await page.getByRole("button", { name: "Create Unplanned Stock" }).click();
  await expect(page).toHaveURL(/wms-stock-adjustment\/new/);
  await page.locator("input[data-fieldname='product']").fill(s.item);
  await page.locator("input[data-fieldname='storage_bin']").fill(s.bins[0]);
  await page.locator("input[data-fieldname='quantity']").fill("5");
  await page.locator("textarea[data-fieldname='reason']").fill("e2e found");
  await page.keyboard.press("Control+s");
  await page.getByRole("button", { name: "Post", exact: true }).click();
  const balances = async () => (await admin(request).get(`/api/resource/WMS Stock Balance?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["storage_bin", "=", s.bins[0]], ["quantity", ">", 0]]))}&fields=["stock_type","quantity"]`)).data;
  await expect.poll(balances).toEqual([{ stock_type: "AVAILABLE", quantity: 5 }]);

  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Stock Overview" }).click();
  await page.locator(".modal.show", { hasText: "Selection - Stock Overview" }).getByRole("button", { name: /Execute/ }).click();
  await expect(page.locator(".wms-mon-stock-table tbody tr", { hasText: s.bins[0] }).first()).toBeVisible();
  await page.locator(".wms-mon-stock-table th.wms-grid-corner").click();
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Posting Change" }).click();
  await expect(page).toHaveURL(/wms-posting-change\/new/);
  await page.locator("input[data-fieldname='to_stock_type']").fill("WAREHOUSE_BLOCKED");
  await page.locator("textarea[data-fieldname='reason']").fill("e2e hold");
  await page.keyboard.press("Control+s");
  await page.getByRole("button", { name: "Post", exact: true }).click();
  await expect.poll(balances).toEqual([{ stock_type: "WAREHOUSE_BLOCKED", quantity: 5 }]);
  expect(errors).toEqual([]);
});
