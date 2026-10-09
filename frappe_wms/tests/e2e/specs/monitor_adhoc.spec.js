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
  await rows.nth(0).locator("th.wms-grid-rowhead").click();                  // mark both rows, then Mass Change puts the destination into all of them
  await rows.nth(1).locator("th.wms-grid-rowhead").click({ modifiers: ["Control"] });
  await page.locator(".wb-mass").click();
  const mass = page.locator(".modal.show");
  await mass.locator("input[data-fieldname='destination_bin']").fill(s.bins[1]);
  await mass.locator("textarea[data-fieldname='reason']").fill("e2e re-slotting");
  await mass.getByRole("button", { name: /Apply to marked rows/ }).click();
  await expect(rows.nth(0).locator("input[data-f='destination_bin']")).toHaveValue(s.bins[1]);
  await expect(rows.nth(1).locator("input[data-f='destination_bin']")).toHaveValue(s.bins[1]);
  await rows.nth(0).locator("input[data-f='destination_bin']").press("Enter");   // Enter fills the storage type and section of the bin entered
  await expect(rows.nth(0).locator("input[data-f='destination_storage_type']")).not.toHaveValue("");
  await expect(page.locator(".wb-res").first()).toContainText("\u2192");
  await rows.nth(0).locator("input[data-f='destination_bin']").fill("");        // nothing entered and nothing to determine it: Check says why
  await rows.nth(0).locator("input[data-f='destination_bin']").dispatchEvent("change");
  await rows.nth(0).locator("input[data-f='destination_storage_type']").fill("");
  await rows.nth(0).locator("th.wms-grid-rowhead").click();
  await page.locator(".wb-check").click();
  await expect(page.locator(".wb-status")).toContainText("cannot be created");
  await rows.nth(0).locator("input[data-f='destination_bin']").fill(s.bins[1]);
  await rows.nth(0).locator("input[data-f='destination_bin']").press("Enter");
  await expect(rows.nth(0).locator("input[data-f='destination_storage_type']")).not.toHaveValue("");
  await rows.nth(0).locator("th.wms-grid-rowhead").click();                  // switch the marked row to the form view: n / N
  await page.locator(".wb-toggle").click();
  await expect(page.locator(".wb-detail")).toContainText("1 / 2");
  await expect(page.locator(".wb-detail input[data-f='destination_bin']")).toHaveValue(s.bins[1]);
  await page.locator(".wb-toggle").click();                                    // back to the list
  await rows.nth(0).locator("th.wms-grid-rowhead").click();
  await rows.nth(1).locator("th.wms-grid-rowhead").click({ modifiers: ["Control"] });
  await expect(page.locator(".wms-grid-selcount, .wb-table")).toBeVisible();
  await page.locator(".wb-create").click();
  await expect(page.locator(".wb-status")).toContainText("2 task(s) created");
  await expect.poll(async () => (await admin(request).get(`/api/resource/Warehouse Task?filters=${encodeURIComponent(JSON.stringify([["warehouse", "=", s.warehouse], ["stock_type_from", "in", ["WAREHOUSE_BLOCKED", "AVAILABLE"]], ["destination_bin", "=", s.bins[1]], ["reason", "=", "e2e re-slotting"]]))}&fields=["name"]`)).data.length).toBe(2);
  await expect(page.locator(".wb-pane")).toContainText("WT-");                   // the Created WTs tab lists them
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
  for (const [tx, title] of [["scrap", "Stock Overview"], ["hublock", "Handling Units"], ["tasks", "Warehouse Tasks"], ["wo", "Warehouse Orders"], ["wave", "Waves"]]) {
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

test("posting change worklist: mass change fills the marked rows", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto("/app/wms-posting");
  await page.locator(".wb-wh").selectOption(s.warehouse);
  await expect(page.locator(".wb-title")).toContainText("Posting Change in Warehouse Number");
  await page.locator(".wb-by").selectOption("storage_bin");
  await page.locator(".wb-value").fill(s.bins[0]);
  await page.locator(".wb-go").click();
  const rows = page.locator(".wb-table tbody tr");
  await expect(rows).toHaveCount(2);
  await rows.nth(0).locator("th.wms-grid-rowhead").click();
  await page.locator(".wb-mass").click();
  await page.locator(".modal.show input[data-fieldname='to_stock_type']").fill("WAREHOUSE_BLOCKED");
  await page.locator(".modal.show textarea[data-fieldname='reason']").fill("e2e hold");
  await page.locator(".modal.show").getByRole("button", { name: /Apply to marked rows/ }).click();
  await expect(rows.nth(0).locator("input[data-f='to_stock_type']")).toHaveValue("WAREHOUSE_BLOCKED");
  await expect(rows.nth(1).locator("input[data-f='to_stock_type']")).toHaveValue("");     // only the marked row
  expect(errors).toEqual([]);
});
