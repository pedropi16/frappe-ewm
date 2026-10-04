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

test("packing center: tree, drag to repack, create HUs", async ({ page }) => {
  const s = seed();
  bench("cleanup"); bench("packing_stock");
  await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } });
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Packing Center" }).click();
  const dialog = page.locator(".modal.show", { hasText: "Selection - Packing Center" });
  await expect(dialog).toBeVisible();
  await dialog.locator(".wms-sel-row[data-field='name'] .wms-sel-from").fill(`${s.warehouse}-A1`);
  await dialog.getByRole("button", { name: /Execute/ }).click();
  await expect(dialog).toBeHidden();

  // section > bin > HU > product
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-section")).toHaveCount(1);
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-hu")).toHaveCount(2);
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-product")).toHaveCount(1);

  // mark the product line -> side panel shows it; drag it onto the other HU, repack 2 of the 6
  const product = page.locator(".wms-pc-grid tbody tr.wms-hier-product");
  await page.locator(".wms-pc-tab", { hasText: "Details" }).click();
  await product.locator("th.wms-grid-rowhead").click();
  await expect(page.locator(".wms-pc-side")).toContainText("Open form");
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/packing-details.png` });
  const pc2 = page.locator(".wms-pc-grid tbody tr.wms-hier-hu", { hasText: "E2EPC2" });
  // right button: asks how much - 2 of the 6
  const from = await product.locator(".wms-row-drag").boundingBox(), to = await pc2.boundingBox();
  await page.mouse.move(from.x + 8, from.y + 8);
  await page.mouse.down({ button: "right" });
  await page.mouse.move(to.x + 60, to.y + 8, { steps: 8 });
  await page.mouse.up({ button: "right" });
  const qdialog = page.locator(".modal.show", { hasText: "Repack into" });
  await expect(qdialog).toBeVisible();
  await qdialog.locator("input").fill("2");
  await qdialog.getByRole("button", { name: "Repack" }).click();
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-product")).toHaveCount(2);
  // left button: everything that is left, no dialog
  await page.locator(".wms-pc-grid tbody tr.wms-hier-product").first().locator(".wms-row-drag").dragTo(page.locator(".wms-pc-grid tbody tr.wms-hier-hu", { hasText: "E2EPC2" }));
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-product")).toHaveCount(1);
  await expect(page.locator(".modal.show", { hasText: "Repack into" })).toHaveCount(0);

  // Difference tab: 1 unit of the 2 now in E2EPC2 is missing
  const moved = page.locator(".wms-pc-grid tbody tr.wms-hier-product").last();
  await moved.locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-pc-tab", { hasText: "Difference" }).click();
  await page.locator('[data-pane="difference"] .wms-pc-q').fill("1");
  await page.locator('[data-pane="difference"] .wms-pc-go').click();
  await page.locator(".modal.show").getByRole("button", { name: /Yes|OK/ }).click();
  await expect(page.getByText("Posted 1 difference")).toBeVisible();

  // create 2 HUs from a packing material in the bin
  await page.locator(".wms-pc-tab", { hasText: "Create HU" }).click();
  await page.locator(".wms-pc-material").selectOption("E2E-BOX");
  await page.locator(".wms-pc-bin input").fill(`${s.warehouse}-A1`);
  await page.locator(".wms-pc-qty").fill("2");
  await page.locator(".wms-pc-create").click();
  await expect(page.locator(".wms-pc-grid tbody tr.wms-pc-new")).toHaveCount(2);
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/packing.png` });
  bench("cleanup");
});

test("packing center: an HU search shows only that HU", async ({ page }) => {
  const s = seed();
  bench("cleanup"); bench("packing_stock");
  await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } });
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Packing Center" }).click();
  const dialog = page.locator(".modal.show", { hasText: "Selection - Packing Center" });
  await dialog.locator(".wms-sel-row[data-field='handling_unit'] .wms-sel-from").fill("E2EPC1");
  await dialog.getByRole("button", { name: /Execute/ }).click();
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-hu")).toHaveCount(1);
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-hu")).toContainText("E2EPC1");
  await expect(page.locator(".wms-pc-grid tbody tr.wms-hier-product")).toHaveCount(1); // its contents, nothing else from the bin
  bench("cleanup");
});
