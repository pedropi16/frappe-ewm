// Barcode-scan input handling that does not depend on where focus happens to be.
//
// A Bluetooth HID scanner (Netum etc.) is just a keyboard: it "types" the code very fast and ends with Enter. Two
// consequences drive this module: (1) the keystrokes go wherever focus is, which on a phone is often nowhere useful,
// and (2) a scan can land in the wrong field (a quantity box). Burst detection tells a scan from a human by typing
// speed, so a scan is always routed to the field the screen is waiting on.

// Symbology identifiers some scanners prepend (AIM ID, "]C1" = Code 128, "]E0" = EAN, "]Q3" = QR ...).
const AIM_ID = /^\][A-Za-z][0-9]/;
// Control characters scanners add around a payload: STX/ETX, GS/RS/US separators, CR/LF/TAB, NUL, DEL.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/g;

export function normalizeScan(raw) {
  let s = String(raw == null ? "" : raw);
  s = s.replace(CONTROL, "").trim();
  if (AIM_ID.test(s)) s = s.slice(3);
  return s.trim();
}

// A burst is a scan when it is long enough and every keystroke arrived faster than a human types.
export const BURST_MIN_CHARS = 4;
export const BURST_MAX_AVG_GAP_MS = 45;
export const BURST_MAX_GAP_MS = 120;

export function createBurstTracker(now = () => performance.now()) {
  let chars = "";
  let first = 0;
  let last = 0;
  let broken = false;
  return {
    // Feed every printable keydown. Returns void; call finish() on Enter.
    key(ch) {
      const t = now();
      if (chars && t - last > BURST_MAX_GAP_MS) { chars = ""; broken = false; }
      if (!chars) { first = t; broken = false; }
      chars += ch;
      last = t;
    },
    // On Enter: the burst text if it looks like a scan, otherwise null. Always resets.
    finish() {
      const t = last;
      const n = chars.length;
      const text = chars;
      const looksScanned = n >= BURST_MIN_CHARS && !broken && n > 1 && ((t - first) / (n - 1)) <= BURST_MAX_AVG_GAP_MS;
      chars = ""; broken = false;
      return looksScanned ? text : null;
    },
    peek() { return chars; },
    reset() { chars = ""; broken = false; },
  };
}
