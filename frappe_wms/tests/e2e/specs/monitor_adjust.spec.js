import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Desktop WMS Monitor, Stock Overview: create unplanned stock, then change its stock type and scrap it from the marked rows.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adjustments"); });

test("stock overview: unplanned stock, posting change, scrap", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Stock Overview" }).click();
  const popup = page.locator(".modal.show", { hasText: "Selection - Stock Overview" });
  await expect(popup).toBeVisible();
  await popup.getByRole("button", { name: /Execute/ }).click();

  await page.getByRole("button", { name: "Create Unplanned Stock" }).click();
  const create = page.locator(".modal.show", { hasText: "Create Unplanned Stock" });
  await create.locator("input[data-fieldname='product']").fill(s.item);
  await create.locator("input[data-fieldname='quantity']").fill("5");
  await create.locator("input[data-fieldname='storage_bin']").fill(s.bins[0]);
  await create.locator("textarea[data-fieldname='reason']").fill("e2e found");
  await create.getByRole("button", { name: "Create", exact: true }).click();
  await expect(create).toBeHidden();

  await page.getByRole("button", { name: /Refresh/ }).click();
  const row = page.locator(".wms-mon-stock-table tbody tr", { hasText: s.bins[0] }).first();
  await expect(row).toBeVisible();
  await page.locator(".wms-mon-stock-table th.wms-grid-corner").click();
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Posting Change" }).click();
  const pc = page.locator(".modal.show", { hasText: "Posting Change of" });
  await pc.locator("input[data-fieldname='to_stock_type']").fill("WAREHOUSE_BLOCKED");
  await pc.locator("textarea[data-fieldname='reason']").fill("e2e hold");
  await pc.getByRole("button", { name: "Post", exact: true }).click();
  await expect(pc).toBeHidden();
  const balances = async () => (await admin(request).get(`/api/resource/WMS Stock Balance?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["storage_bin", "=", s.bins[0]], ["quantity", ">", 0]]))}&fields=["stock_type","quantity"]`)).data;
  await expect.poll(balances).toEqual([{ stock_type: "WAREHOUSE_BLOCKED", quantity: 5 }]);

  await page.getByRole("button", { name: /Refresh/ }).click();
  await expect(page.locator(".wms-mon-stock-table tbody tr", { hasText: "WAREHOUSE_BLOCKED" }).first()).toBeVisible();
  await page.locator(".wms-mon-stock-table th.wms-grid-corner").click();
  await page.locator(".wms-mon-stock-table .wms-grid-actionbar").getByRole("button", { name: "Scrap" }).click();
  const scrap = page.locator(".modal.show", { hasText: "Scrap " });
  await scrap.locator("textarea[data-fieldname='reason']").fill("e2e broken");
  await scrap.getByRole("button", { name: "Scrap", exact: true }).click();
  await expect(scrap).toBeHidden();
  await expect.poll(balances).toEqual([]);
  expect(errors).toEqual([]);
});
