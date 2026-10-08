import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// The Outbound Delivery form is the maintenance screen: header status, item statuses, tabs, and the actions that drive the delivery.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let demo;
test.beforeAll(() => { bench("cleanup_outbound"); bench("cleanup"); demo = JSON.parse(bench("outbound_demo").split("\n").find((l) => l.startsWith("E2E_OUTBOUND ")).slice(13)); });
test.afterAll(() => { bench("cleanup_outbound"); bench("cleanup"); });

test("outbound delivery screen: allocate, create pick tasks, read the tabs", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto(`/app/wms-outbound-delivery/${demo.delivery}`);
  await expect(page.locator(".indicator-pill", { hasText: "Allocation: Not Allocated" })).toBeVisible();
  for (const tab of ["Items", "Status", "Dates / Times", "Locations", "Partner", "Reference Documents", "HU", "Transportation Unit"]) await expect(page.locator(".wms-dm-tab", { hasText: new RegExp(`^${tab.replace("/", "\\/")}$`) })).toBeVisible();
  await expect(page.locator(".wms-dm-find")).toBeVisible();   // top: search
  await expect(page.locator(".wms-dm-head")).toContainText("Customer");   // middle: the header data

  await page.getByRole("button", { name: "Allocate Stock" }).click();
  await expect(page.locator(".indicator-pill", { hasText: "Allocation: Fully Allocated" })).toBeVisible();
  await page.getByRole("button", { name: "Create Pick Tasks" }).click();
  await page.locator(".wms-dm-tab", { hasText: /^Status$/ }).click();
  await expect(page.locator("tr:visible", { hasText: "Pick" }).first()).toBeVisible();
  await page.locator(".wms-dm-tab", { hasText: /^Locations$/ }).click();
  await expect(page.locator("tr:visible", { hasText: "E2E-WH-A1" }).first()).toBeVisible();
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/outbound-screen.png`, fullPage: true });
  // the plain form stays reachable, and any other link to the delivery lands back on this screen
  await page.getByRole("button", { name: /More/ }).click();
  await page.locator("a:visible", { hasText: "Standard Form" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/(app|desk)/outbound-delivery/${demo.delivery}`));
  await page.goto(`/app/outbound-delivery/${demo.delivery}`);
  await expect(page).toHaveURL(new RegExp(`/(app|desk)/wms-outbound-delivery/${demo.delivery}`));
  expect(errors).toEqual([]);
});
