import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { chromium } from "@playwright/test";
import { libEnv, FIXTURE_DIR, CAMERA_FILE, CAMERA_CODE } from "./env.js";

// Renders CAMERA_CODE as a QR code with the vendored ZXing writer and saves it as a looping MJPEG that Chromium's
// fake camera plays back (this ZXing build can only *write* QR) - so the camera scan path (the same ZXing decode iOS uses) is tested without hardware.
export async function makeCameraFixture() {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const zx = readFileSync(new URL("../../public/js/wms_rf/vendor/zxing-library.min.js", import.meta.url), "utf8");
  const browser = await chromium.launch({ env: libEnv });
  const page = await browser.newPage();
  await page.setContent("<canvas id=c width=1280 height=720></canvas>");
  await page.addScriptTag({ content: zx });
  const dataUrl = await page.evaluate((code) => {
    const c = document.getElementById("c"), g = c.getContext("2d");
    g.fillStyle = "#fff"; g.fillRect(0, 0, 1280, 720);
    const m = new ZXing.MultiFormatWriter().encode(code, ZXing.BarcodeFormat.QR_CODE, 500, 500, new Map());
    const n = m.getWidth(); const unit = Math.floor(500 / n); const off = Math.floor((1280 - n * unit) / 2), offY = Math.floor((720 - n * unit) / 2);
    g.fillStyle = "#000";
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (m.get(x, y)) g.fillRect(off + x * unit, offY + y * unit, unit, unit);
    return c.toDataURL("image/jpeg", 0.95);
  }, CAMERA_CODE);
  await browser.close();
  const jpeg = Buffer.from(dataUrl.split(",")[1], "base64");
  writeFileSync(CAMERA_FILE, Buffer.concat(Array.from({ length: 15 }, () => jpeg)));
}
