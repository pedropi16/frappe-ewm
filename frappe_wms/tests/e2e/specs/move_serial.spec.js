import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { loginOperator, logon, openApp, freeResources, scan, enter, notice, view, seed } from "../helpers.js";

// A move of only some of a serial-managed stock cannot be guessed: the review step asks for the serial numbers (SAP: confirm in the foreground).
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let item;
test.beforeAll(() => { bench("cleanup"); item = bench("serial_stock").split("\n").find((l) => l.startsWith("E2E_SERIAL_ITEM ")).slice(16); });
test.afterAll(() => bench("cleanup"));
test.beforeEach(async ({ page, request }) => { await freeResources(request); await loginOperator(page); await logon(page); });

test("Move: two of three serial numbers - the review asks which ones", async ({ page }) => {
  await openApp(page, "#/move-manual");
  await scan(page, item);
  await page.locator('[data-fk="quantity"]').fill("2"); await enter(page);
  await scan(page, "E2E-WH-A1");
  await expect(page).toHaveURL(/\/destination$/);
  await scan(page, "E2E-WH-B1");
  await expect(page).toHaveURL(/\/review$/);
  await expect(view(page)).toContainText("Serial numbers");
  await scan(page, "E2ESNM0");
  await scan(page, "NOT-A-SERIAL");
  await expect(view(page)).toContainText("not in this stock");
  await scan(page, "E2ESNM2");
  await expect(view(page)).toContainText("2 of 2");
  await page.getByRole("button", { name: /Move$/ }).click();
  await expect(notice(page)).toContainText(/Moved 2/);
});
