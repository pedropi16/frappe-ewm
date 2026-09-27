// Per-device preferences (sound, theme, on-screen keyboard for scan fields). localStorage, never required to work.
const KEY = "wms.rf.prefs";
const DEFAULTS = { sound: true, vibrate: true, theme: "auto", keyboard: false };

function read() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { return { ...DEFAULTS }; }
}

let cache = null;
export function prefs() { return cache || (cache = read()); }
export function setPref(key, value) {
  cache = { ...prefs(), [key]: value };
  try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch (e) { /* private mode - keep in memory */ }
  if (key === "theme") applyTheme();
}
export function applyTheme() {
  const t = prefs().theme;
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}
