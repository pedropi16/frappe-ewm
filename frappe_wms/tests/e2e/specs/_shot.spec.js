import { test } from "@playwright/test";
import { loginOperator, logon, openApp, makeTask, freeResources } from "../helpers.js";
// Screenshot helper, not a test: runs only with SHOT_DIR set (e.g. SHOT_DIR=/tmp/shots npx playwright test _shot).
const DIR = process.env.SHOT_DIR;
test("shots", async ({ page, request }) => {
  test.skip(!DIR, "set SHOT_DIR to take screenshots");
  await freeResources(request);
  await loginOperator(page); await logon(page);
  const t = await makeTask(request, { planned_quantity: 8 });
  await openApp(page);
  await page.screenshot({ path: `${DIR}/s1-menu.png` });
  await openApp(page, "#/tasks/internal");
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${DIR}/s2-tasks.png` });
  await openApp(page, "#/task/" + t.name);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${DIR}/s3-task.png` });
  console.log("URL", page.url());
});
