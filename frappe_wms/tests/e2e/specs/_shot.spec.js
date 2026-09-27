import { test } from "@playwright/test";
import { loginOperator, logon, openApp, makeTask, freeResources } from "../helpers.js";
test("shots", async ({ page, request }) => {
  await freeResources(request);
  await loginOperator(page); await logon(page);
  const t = await makeTask(request, { planned_quantity: 8 });
  await openApp(page);
  await page.screenshot({ path: "/tmp/claude-1000/-home-pino/46678a32-9ab3-49c8-acf3-d24c36746286/scratchpad/s1-menu.png" });
  await openApp(page, "#/tasks/internal");
  await page.waitForTimeout(600);
  await page.screenshot({ path: "/tmp/claude-1000/-home-pino/46678a32-9ab3-49c8-acf3-d24c36746286/scratchpad/s2-tasks.png" });
  await openApp(page, "#/task/" + t.name);
  await page.waitForTimeout(800);
  await page.screenshot({ path: "/tmp/claude-1000/-home-pino/46678a32-9ab3-49c8-acf3-d24c36746286/scratchpad/s3-task.png" });
  console.log("URL", page.url());
});
