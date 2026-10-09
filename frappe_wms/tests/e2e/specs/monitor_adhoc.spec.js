import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, admin } from "../helpers.js";

// Ad Hoc Processing: the advanced selection finds many stock lines, one action creates the tasks for all marked ones.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

test.beforeAll(() => { bench("cleanup"); bench("adhoc_stock"); });
test.afterAll(() => { bench("cleanup"); bench("cleanup_adhoc"); });

test("ad hoc product tasks: find, fill the destination per row, create", async ({ page, request }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-adprod");
  await page.locator(".wb-wh").selectOption(s.warehouse);
  await expect(page.locator(".wb-title")).toContainText(`Warehouse Number ${s.warehouse}`);
  await page.locator(".wb-by").selectOption("storage_bin");
  await page.locator(".wb-value").fill(s.bins[0]);
  await page.locator(".wb-go").click();
  await expect(page.locator(".wb-status")).toContainText("Selection resulted in 2 hit(s)");
  const rows = page.locator(".wb-table tbody tr");
  await expect(rows).toHaveCount(2);
  for (let i = 0; i < 2; i++) {
    await rows.nth(i).locator(".wb-pick").check();
    await rows.nth(i).locator("input[data-f='destination_bin']").fill(s.bins[1]);
    await rows.nth(i).locator("input[data-f='destination_bin']").blur();
  }
  await page.locator(".wb-toggle").click();                                   // detail form of one row: n / N
  await expect(page.locator(".wb-detail")).toContainText("1 / 2");
  await page.locator(".wb-toggle").click();
  await page.locator(".wb-create").click();
  await expect(page.locator(".wb-status")).toContainText("2 task(s) created");
  await expect.poll(async () => (await admin(request).get(`/api/resource/Warehouse Task?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["stock_type_from", "in", ["WAREHOUSE_BLOCKED", "AVAILABLE"]], ["destination_bin", "=", s.bins[1]]]))}&fields=["name"]`)).data.length).toBe(2);
  await expect(page.locator(".wb-pane")).toContainText("Open");                  // Created WTs tab lists them
  await page.locator(".wb-create").click();                                      // nothing selected any more
  await expect(page.locator(".wb-status")).toContainText("Select at least one row");
  expect(errors).toEqual([]);
});

test("advanced search fills the worklist", async ({ page }) => {
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-adprod");
  await page.locator(".wb-wh").selectOption(s.warehouse);
  await page.locator(".wb-adv").click();
  await page.locator(".modal.show", { hasText: "Selection - Stock Overview" }).getByRole("button", { name: /Execute/ }).click();
  await expect(page.locator(".wb-status")).toContainText("Selection resulted in");
  await expect(page.locator(".wb-table tbody tr").first()).toBeVisible();
});

test("every ad hoc transaction opens its selection without a script error", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  for (const [tx, title] of [["posting", "Stock Overview"], ["scrap", "Stock Overview"], ["hublock", "Handling Units"], ["tasks", "Warehouse Tasks"], ["wo", "Warehouse Orders"], ["wave", "Waves"]]) {
    await page.goto(`/app/wms-adhoc/${tx}`);
    await expect(page.locator(".modal.show", { hasText: `Selection - ${title}` })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("the HU worklist page opens", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-adhu");
  await page.locator(".wb-wh").selectOption(s.warehouse);
  await expect(page.locator(".wb-title")).toContainText("Create HU Warehouse Task in Warehouse Number");
  await page.locator(".wb-value").fill("*");
  await page.locator(".wb-go").click();
  await expect(page.locator(".wb-status")).toContainText("Selection resulted in");
  await page.locator(".wb-tab", { hasText: "Master Data/Status" }).click();
  expect(errors).toEqual([]);
});
