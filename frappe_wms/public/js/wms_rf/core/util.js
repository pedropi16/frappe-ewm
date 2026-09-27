// Small pure helpers shared by every module. Nothing here touches the DOM, so it runs unchanged under Node's test runner.

export function flt(v) { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; }
// Parses what an operator types into a quantity box; accepts a decimal comma ("2,5"), which phone keypads produce.
export function parseNum(v) { return flt(String(v == null ? "" : v).trim().replace(",", ".")); }
export function isNumeric(v) { return /^\s*-?\d+([.,]\d+)?\s*$/.test(String(v == null ? "" : v)); }
export function round6(n) { return Math.round(flt(n) * 1e6) / 1e6; }
export function uid() { return Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4); }
export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
export function statusClass(status) { return String(status || "").replace(/[^A-Za-z0-9]+/g, ""); }

export function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.flush = (...args) => { clearTimeout(t); fn(...args); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

// Formats a quantity the way a warehouse floor reads it: no float noise, no trailing zeros.
export function fmtQty(v) {
  const n = round6(v);
  return Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));
}

// Case- and whitespace-insensitive match of a scanned code against the codes a screen expects. Returns the canonical
// (expected) spelling so callers send the server the exact value it stores, whatever the scanner or keyboard did to case.
export function matchExpected(scanned, expected) {
  const s = String(scanned == null ? "" : scanned).trim().toLowerCase();
  if (!s) return null;
  for (const e of expected || []) {
    if (e && String(e).trim().toLowerCase() === s) return e;
  }
  return null;
}
