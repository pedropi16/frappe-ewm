import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Warehouse Monitor: Production Material Requests view, and "Confirm in Background" on Warehouse Tasks / Warehouse Orders.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });
const out = (text, tag) => JSON.parse(text.split("\n").find((l) => l.startsWith(tag + " ")).slice(tag.length + 1));

let demo, tasks;
test.beforeAll(() => { bench("cleanup_staging"); bench("cleanup"); demo = out(bench("staging_demo"), "E2E_STAGING"); tasks = out(bench("plain_task"), "E2E_TASKS"); });
test.afterAll(() => { bench("cleanup_staging"); bench("cleanup"); });

async function open(page, view, title) {
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-monitor");
  await page.locator(".wms-mon-warehouse").selectOption(s.warehouse);
  await page.locator(".wms-mon-nav-item", { hasText: view }).click();
  await page.locator(".modal.show", { hasText: `Selection - ${title}` }).getByRole("button", { name: /Execute/ }).click();
  return s;
}

test("production material requests: list, items with progress", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await open(page, "Production Material Requests", "Production Material Requests");
  const row = page.locator(".wms-mon-pmr-table tbody tr", { hasText: demo.pmr });
  await expect(row).toBeVisible();
  await row.locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-pmr-table .wms-grid-actionbar").getByRole("button", { name: "Items" }).click();
  const d = page.locator(".modal.show", { hasText: "items" });
  await expect(d).toContainText(demo.item);
  await expect(d).toContainText(demo.psa);
  await expect(d).toContainText("Required");
  expect(errors).toEqual([]);
});

test("confirm in the background: a task, then a whole warehouse order", async ({ page, request }) => {
  const s = await open(page, "Warehouse Tasks", "Warehouse Tasks");
  const status = async (n) => (await admin(request).get(`/api/resource/Warehouse Task/${n}`)).data.status;
  const row = page.locator(".wms-mon-task-table tbody tr", { hasText: tasks[0] });
  await row.locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-task-table .wms-grid-actionbar").getByRole("button", { name: "Confirm in Background" }).click();
  await expect(page.locator(".desk-alert, .alert-container").filter({ hasText: "confirmed in the background" }).first()).toBeVisible();
  await expect.poll(() => status(tasks[0])).toBe("Confirmed");
  expect(await status(tasks[1])).not.toBe("Confirmed");

  // the order of the other task: the remaining task of it is confirmed
  await page.locator(".wms-mon-nav-item", { hasText: "Warehouse Orders" }).click();
  await page.locator(".modal.show", { hasText: "Selection - Warehouse Orders" }).getByRole("button", { name: /Execute/ }).click();
  const order = (await admin(request).get(`/api/resource/Warehouse Task/${tasks[1]}`)).data.warehouse_order;
  const orow = page.locator(".wms-mon-wo-table tbody tr", { hasText: order });
  await orow.locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-wo-table .wms-grid-actionbar").getByRole("button", { name: "Confirm in Background" }).click();
  await expect.poll(() => status(tasks[1])).toBe("Confirmed");

  // nothing left: refused with the popup, dismissed with Enter
  await orow.locator("th.wms-grid-rowhead").click();
  await page.locator(".wms-mon-wo-table .wms-grid-actionbar").getByRole("button", { name: "Confirm in Background" }).click();
  const popup = page.locator(".modal.show", { hasText: "Not possible" });
  await expect(popup).toContainText("Nothing left to confirm");
  await page.waitForTimeout(500);
  await page.keyboard.press("Enter");
  await expect(popup).toBeHidden();
});
