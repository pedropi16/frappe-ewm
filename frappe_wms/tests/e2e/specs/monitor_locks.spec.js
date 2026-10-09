import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { seed, loginOperator } from "../helpers.js";

// Display / change mode: the delivery screen opens for display; Change locks it, a second user can only display it until the first goes back to display.
const bench = (fn) => execFileSync("bench", ["--site", process.env.SITE || "wms.local", "execute", `frappe_wms.tests.e2e.seed.${fn}`],
  { cwd: process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`, encoding: "utf8" });

let demo;
test.beforeAll(() => { bench("cleanup_outbound"); demo = JSON.parse(bench("outbound_demo").split("\n").find((l) => l.startsWith("E2E_OUTBOUND ")).slice(13)); });
test.afterAll(() => bench("cleanup_outbound"));

test("a delivery changed by one user is display-only for another", async ({ page, browser }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const s = seed();
  expect((await page.request.post("/api/method/login", { form: { usr: s.admin, pwd: s.admin_password } })).ok()).toBeTruthy();
  await page.goto(`/app/wms-outbound-delivery/${demo.delivery}`);
  await expect(page.getByRole("button", { name: "Allocate Stock" })).toHaveCount(0);   // display mode: no actions
  await page.getByRole("button", { name: "Change", exact: true }).click();
  await expect(page.getByRole("button", { name: "Display", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Allocate Stock|Create Pick Tasks/ }).first()).toBeVisible();

  const other = await browser.newContext({ baseURL: page.url().split("/app")[0] });
  const op = await other.newPage();
  await loginOperator(op);
  await op.goto(`/app/wms-outbound-delivery/${demo.delivery}`);
  await expect(op.getByRole("button", { name: /Display only: being changed by/ })).toBeVisible();
  await expect(op.getByRole("button", { name: /Allocate Stock|Create Pick Tasks/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Display", exact: true }).click();
  await op.getByRole("button", { name: /Display only/ }).click();   // reloads the screen: free again
  await expect(op.getByRole("button", { name: "Change", exact: true })).toBeVisible();
  await other.close();
  expect(errors).toEqual([]);
});
