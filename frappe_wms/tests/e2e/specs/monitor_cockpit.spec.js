import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed } from "../helpers.js";

// Desktop WMS Monitor: Shipping & Receiving cockpit - plan a truck for an inbound delivery, add a second delivery, check in.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let deliveries;
test.beforeAll(() => {
  bench("cleanup");
  deliveries = JSON.parse(bench("yard_demo").split("\n").find((l) => l.startsWith("E2E_YARD ")).slice(9)).inbound;
});
test.afterAll(() => bench("cleanup"));

test("cockpit: plan a truck, add a delivery, check in", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-yard");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: "Shipping & Receiving" }).click();

  const row = page.locator("tr", { hasText: deliveries[0] });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Plan Truck" }).click();
  const dialog = page.locator(".modal.show", { hasText: "Plan Truck for" });
  await dialog.locator("input[data-fieldname='vehicle_registration']").fill("E2E-TRUCK");
  await dialog.getByRole("button", { name: "Plan", exact: true }).click();

  const truck = page.locator("tr:visible", { hasText: "E2E-TRUCK" });
  await expect(truck).toBeVisible();
  await expect(truck).toContainText("Planned");
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/cockpit.png` });

  await truck.getByRole("button", { name: "Add Delivery" }).click();
  const add = page.locator(".modal.show", { hasText: "Add a delivery to" });
  await add.locator("input[data-fieldname='ref']").fill(deliveries[1]);
  await add.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.locator(".msgprint, .modal.show", { hasText: "Error" })).toHaveCount(0);
  await expect(page.locator("tr:visible", { hasText: "E2E-TRUCK" })).toContainText("2");

  await truck.getByRole("button", { name: "Check In" }).click();
  await page.locator(".modal.show").getByRole("button", { name: "Check in", exact: true }).click();
  await expect(page.locator("tr:visible", { hasText: "E2E-TRUCK" })).toContainText("Checked In");
  expect(errors).toEqual([]);
});

test("every Monitor view opens without a script error", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  for (const route of ["wms-monitor", "wms-yard", "wms-packing-center", "wms-kitting", "wms-billing"]) {
    await page.goto(`/app/${route}`);
    await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
    await page.waitForTimeout(700);
    if (await page.locator(".modal.show").count()) await page.keyboard.press("Escape");   // the first view opens its selection popup
    const items = page.locator(".wms-mon-nav-item");
    const n = await items.count();
    for (let i = 0; i < n; i++) {
      await items.nth(i).click();
      // a selection popup (Stock Overview and the like) is part of opening the view; close it
      await page.waitForTimeout(700);
      const modal = page.locator(".modal.show");
      if (await modal.count()) await page.keyboard.press("Escape");
    }
  }
  expect(errors).toEqual([]);
});
