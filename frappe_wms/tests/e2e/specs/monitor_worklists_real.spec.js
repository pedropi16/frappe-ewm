import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// The worklists on real ledger stock: after Create / Post the rows show what happened (the stock moved, was scrapped) without a refresh.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let hu;
test.beforeEach(() => { bench("cleanup"); hu = bench("worklist_stock").split("\n").find((l) => l.startsWith("E2E_HU ")).slice(7); });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adhoc"); });

async function open(page, route, by, value) {
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto(`/app/${route}`);
  await page.locator(".wb-wh").selectOption(s.warehouse);
  await page.locator(".wb-by").selectOption(by);
  await page.locator(".wb-value").fill(value);
  await page.locator(".wb-go").click();
  return s;
}

test("adhu: the handling unit moves and its row shows the new bin", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = await open(page, "wms-adhu", "handling_unit", "E2EPC9");
  const row = page.locator(".wb-table tbody tr").first();
  await expect(row).toContainText(s.bins[0]);
  await row.locator("th.wms-grid-rowhead").click();
  await row.locator("input[data-f='destination_bin']").fill(s.bins[1]);
  await row.locator("input[data-f='confirm']").check();
  await page.locator(".wb-create").click();
  await expect(page.locator(".wb-status")).toContainText("1 task(s) created");
  await expect(page.locator(".wb-table tbody tr").first()).toContainText(s.bins[1]);          // the source bin column now reads the new bin
  await expect(page.locator(".wb-table tbody tr").first()).not.toContainText(s.bins[0]);
  expect(errors).toEqual([]);
});

test("adprod: a partial move leaves the rest in the row", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = await open(page, "wms-adprod", "storage_bin", seed().bins[0]);
  const rows = page.locator(".wb-table tbody tr");
  await expect(rows).toHaveCount(2);
  const loose = rows.filter({ hasNotText: "E2EPC9" });
  await loose.locator("th.wms-grid-rowhead").click();
  await loose.locator("input[data-f='quantity']").fill("2");
  await loose.locator("input[data-f='destination_bin']").fill(s.bins[1]);
  await loose.locator("input[data-f='confirm']").check();
  await page.locator(".wb-create").click();
  await expect(page.locator(".wb-status")).toContainText("1 task(s) created");
  await expect(rows.filter({ hasNotText: "E2EPC9" }).filter({ has: page.locator("td", { hasText: /^3$/ }) })).toHaveCount(1);                             // 5 - 2 left in the source bin
  expect(errors).toEqual([]);
});

test("scrapping: the scrapped quantity leaves the row", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = await open(page, "wms-scrapping", "storage_bin", seed().bins[0]);
  const rows = page.locator(".wb-table tbody tr");
  const loose = rows.filter({ hasNotText: "E2EPC9" });
  await loose.locator("th.wms-grid-rowhead").click();
  await loose.locator("input[data-f='quantity']").fill("2");
  await loose.locator("input[data-f='reason']").fill("e2e broken");
  await page.locator(".wb-create").click();
  await expect(page.locator(".wb-status")).toContainText("posted");
  await expect(rows.filter({ hasNotText: "E2EPC9" }).filter({ has: page.locator("td", { hasText: /^3$/ }) })).toHaveCount(1);
  expect(errors).toEqual([]);
});
