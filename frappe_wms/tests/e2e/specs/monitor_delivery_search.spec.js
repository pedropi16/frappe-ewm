import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// The delivery screen's own search: it lists the latest deliveries when opened, finds one by number across all warehouses, says so when nothing matches.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let demo;
test.beforeAll(() => { bench("cleanup_outbound"); demo = JSON.parse(bench("outbound_demo").split("\n").find((l) => l.startsWith("E2E_OUTBOUND ")).slice(13)); });
test.afterAll(() => bench("cleanup_outbound"));

test("delivery screen search: latest deliveries, find by number, nothing found", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-outbound-delivery");
  await expect(page.locator(".wms-dm-hits tr", { hasText: demo.delivery }).first()).toBeVisible();   // the latest deliveries are listed without searching
  await page.locator(".wms-dm-value").fill("zzzz-no-such");
  await page.locator(".wms-dm-go").click();
  await expect(page.locator(".wms-dm-hits")).toContainText("No delivery found");
  await page.locator(".wms-dm-by").selectOption("number");
  await page.locator(".wms-dm-value").fill(demo.delivery);
  await page.locator(".wms-dm-value").press("Enter");
  await expect(page.locator(".wms-dm-head")).toContainText("Customer");   // a single hit opens the delivery
  await page.locator(".wms-dm-adv-btn").click();
  await expect(page.locator(".modal.show")).toBeVisible();   // the monitor's advanced selection
  expect(errors).toEqual([]);
});
