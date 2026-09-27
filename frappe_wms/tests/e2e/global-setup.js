import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { makeCameraFixture } from "./make-camera-fixture.js";

export default async function globalSetup() {
  const bench = process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`;
  const site = process.env.SITE || "wms.local";
  const out = execFileSync("bench", ["--site", site, "execute", "frappe_wms.tests.e2e.seed.run"], { cwd: bench, encoding: "utf8" });
  const line = out.split("\n").find((l) => l.startsWith("E2E_SEED "));
  if (!line) throw new Error("seed did not report:\n" + out);
  writeFileSync(new URL("./.seed.json", import.meta.url), line.slice("E2E_SEED ".length));
  await makeCameraFixture();
  execFileSync("bench", ["--site", site, "execute", "frappe_wms.tests.e2e.seed.cleanup"], { cwd: bench, encoding: "utf8" });
}
