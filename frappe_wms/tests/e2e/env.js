import { existsSync } from "node:fs";
// Headless Chromium needs libasound; on a box without it, unpack the .deb into PW_LIBS (see README "Testing the scanner app").
const PW_LIBS = process.env.PW_LIBS || `${process.env.HOME}/.local/pw-libs/usr/lib/x86_64-linux-gnu`;
export const libEnv = existsSync(PW_LIBS) ? { ...process.env, LD_LIBRARY_PATH: `${PW_LIBS}:${process.env.LD_LIBRARY_PATH || ""}` } : undefined;
export const FIXTURE_DIR = new URL("./.fixtures/", import.meta.url).pathname;
export const CAMERA_FILE = `${FIXTURE_DIR}barcode.mjpeg`;
export const CAMERA_CODE = "E2E-WH-A1";
