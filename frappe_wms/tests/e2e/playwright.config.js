import { libEnv, CAMERA_FILE } from "./env.js";
import { defineConfig, devices } from "@playwright/test";

// Runs the scanner app against a dev bench site. Defaults match this repo's dev setup; override with env vars:
//   BENCH_DIR=~/frappe-bench SITE=wms.local PORT=18001 npx playwright test
const PORT = process.env.PORT || "18001";
const BENCH_DIR = process.env.BENCH_DIR || `${process.env.HOME}/frappe-bench`;
const SITE = process.env.SITE || "wms.local";
export default defineConfig({
  testDir: "./specs",
  timeout: 60_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./global-setup.js",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { env: libEnv, args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
  },
  webServer: {
    command: `bench --site ${SITE} serve --port ${PORT} --noreload`,
    cwd: BENCH_DIR,
    url: `http://localhost:${PORT}/api/method/ping`,
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    // iPhone-sized viewport; Chromium engine (WebKit is not installed here) - iOS-specific behaviour is covered by the
    // manual checklist in README "Testing the scanner app".
    { name: "phone", testIgnore: /camera|monitor/, use: { ...devices["Pixel 7"], viewport: { width: 390, height: 780 } } },
    // Desktop WMS Monitor (Frappe desk page).
    { name: "desktop", testMatch: /monitor/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    // Camera scanning: Chromium's fake camera plays back a QR code (made in global-setup).
    { name: "camera", testMatch: /camera/, use: { ...devices["Pixel 7"], viewport: { width: 390, height: 780 },
      launchOptions: { env: libEnv, args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-video-capture=${CAMERA_FILE}`] } } },
  ],
});
