import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// Desktop WMS Monitor: selection popup, grouped Stock Overview, details panel, cross links.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); bench("monitor_stock"); });
test.afterAll(() => bench("cleanup"));

test("stock overview: popup, grouping, details, links", async ({ page }) => {
  const s = seed();
  const r = await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } });
  expect(r.ok()).toBeTruthy();
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Stock Overview" }).click();

  // the selection comes up as a popup first; the page itself only has the compact bar
  const dialog = page.locator(".modal.show", { hasText: "Selection - Stock Overview" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /Execute/ }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".wms-sel-compact")).toBeVisible();

  // 6 serial lines collapse into one row per allocated/free state
  const rows = page.locator(".wms-mon-stock-table tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(page.locator(".wms-mon-stock-table tbody").getByText("Allocated")).toBeVisible();
  await expect(page.locator(".wms-mon-stock-table tbody a[href^='/app/storage-bin/']").first()).toBeVisible();

  // mark a row, press Details -> serial numbers
  await rows.first().locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Details" }).click();
  await expect(page.locator(".wms-detail-panel .wms-chip")).toHaveCount(2);

  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/stock.png` });

  // ungroup: back to one line per serial
  for (const dim of ["storage_type", "storage_bin", "product"]) await page.locator(`.wms-stock-group [data-dim=${dim}]`).uncheck();
  await expect(rows).toHaveCount(6);

  // jump to Stock Movements for the marked rows (no popup)
  await rows.first().locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Movements" }).click();
  await expect(page.locator(".wms-mon-nav-item.active")).toHaveText("Stock Movements");
  await expect(page.locator(".modal.show")).toHaveCount(0);
  await page.screenshot({ path: `${process.env.SHOT_DIR || "test-results"}/monitor.png`, fullPage: false });
});
