import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// A posting change made on the worklist shows its result at once: the old row shrinks, the stock left behind appears with its new stock type (no page refresh).
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); bench("posting_stock"); });
test.afterAll(() => { bench("cleanup"); });

test("posting change worklist shows the new stock type without a refresh", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-posting");
  await page.locator(".wb-wh").selectOption(s.warehouse);
  await page.locator(".wb-by").selectOption("storage_bin");
  await page.locator(".wb-value").fill(s.bins[0]);
  await page.locator(".wb-go").click();
  const rows = page.locator(".wb-table tbody tr");
  await expect(rows).toHaveCount(1);
  await rows.nth(0).locator("th.wms-grid-rowhead").click();
  await rows.nth(0).locator("input[data-f='quantity']").fill("2");
  await rows.nth(0).locator("input[data-f='to_stock_type']").fill("WAREHOUSE_BLOCKED");
  await rows.nth(0).locator("input[data-f='reason']").fill("e2e damaged");
  await page.locator(".wb-create").click();
  await expect(rows).toHaveCount(2);
  await expect(page.locator(".wb-table tbody")).toContainText("WAREHOUSE_BLOCKED");
  await expect(page.locator(".wb-table tbody tr", { hasText: "AVAILABLE" }).first()).toContainText("3");
  expect(errors).toEqual([]);
});
