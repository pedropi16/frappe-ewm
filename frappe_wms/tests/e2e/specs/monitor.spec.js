import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, makeTask } from "../helpers.js";

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

  // step 1: one row per product / bin / stock type / document - allocated part on its own row
  const rows = page.locator(".wms-mon-stock-table tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "E2E-OBD-X" })).toHaveCount(1);
  await expect(page.locator(".wms-mon-stock-table tbody a[href^='/app/storage-bin/']").first()).toBeVisible();
  await expect(page.locator(".wms-mon-stock-table tfoot")).toContainText("6");

  // step 2: mark both rows, Expand -> per HU (loose stock here), with GR date/time
  await page.locator(".wms-mon-stock-table th.wms-grid-corner").click();
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Expand" }).click();
  const hu = page.locator(".wms-detail-lines");
  await expect(hu.locator("tbody tr")).toHaveCount(2);
  await expect(hu).toContainText("2026-10-01 08:30");
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/stock.png` });

  // step 3: mark them, Serial Numbers -> the 6 serials
  await hu.locator("th.wms-grid-corner").click();
  await hu.locator(".wms-grid-actionbar").getByRole("button", { name: "Serial Numbers" }).click();
  await expect(page.locator(".wms-detail-serials-host tbody tr")).toHaveCount(6);

  // a new search drops the expanded panels (they belong to the old result)
  await page.getByRole("button", { name: /Refresh/ }).click();
  await expect(page.locator(".wms-detail-panel")).toHaveCount(0);
  await expect(rows).toHaveCount(2);

  // step 1 again: mark a row -> Movements
  await page.locator(".wms-mon-stock-table th.wms-grid-corner").click();
  // jump to Stock Movements for the marked rows (no popup)
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Movements" }).click();
  await expect(page.locator(".wms-mon-nav-item.active")).toHaveText("Stock Movements");
  await expect(page.locator(".modal.show")).toHaveCount(0);
  await page.screenshot({ path: `${process.env.SHOT_DIR || "test-results"}/monitor.png`, fullPage: false });
});

test("every view: Details shows the whole record", async ({ page, request }) => {
  const s = seed();
  const task = await makeTask(request);
  await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } });
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Warehouse Tasks" }).click();
  const dialog = page.locator(".modal.show", { hasText: "Selection - Warehouse Tasks" });
  await dialog.getByRole("button", { name: /Execute/ }).click();
  const row = page.locator(".wms-mon-task-table tbody tr", { hasText: task.name });
  await row.locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-task-table .wms-grid-actionbar").getByRole("button", { name: "Details" }).click();
  await expect(page.locator(".wms-sel-detail .wms-detail-panel", { hasText: task.name })).toBeVisible();
  await expect(page.locator(".wms-sel-detail .wms-detail-panel").getByText("Planned Quantity")).toBeVisible();
});
