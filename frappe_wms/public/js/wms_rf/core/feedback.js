import { prefs } from "#wms/core/prefs.js";

// Warehouse floors are loud and operators look at the item, not the screen: every scan outcome needs a tone + colour
// flash (+ vibration where the browser has it - Android does, iOS Safari does not). Tones are synthesized, no assets.
let ctx = null;

function audio() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try { ctx = new AC(); } catch (e) { ctx = null; }
  return ctx;
}

// iOS only lets audio start from a user gesture; a scanner keystroke and a tap both count, so unlock on the first one.
export function unlockAudio() {
  const c = audio();
  if (c && c.state === "suspended") c.resume().catch(() => {});
}

function tone(freq, start, dur, type = "sine", gain = 0.18) {
  const c = audio();
  if (!c) return;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0, c.currentTime + start);
  g.gain.linearRampToValueAtTime(gain, c.currentTime + start + 0.01);
  g.gain.linearRampToValueAtTime(0, c.currentTime + start + dur);
  osc.connect(g); g.connect(c.destination);
  osc.start(c.currentTime + start);
  osc.stop(c.currentTime + start + dur + 0.02);
}

function flash(cls) {
  const b = document.body;
  b.classList.remove("flash-ok", "flash-warn", "flash-err");
  void b.offsetWidth; // restart the animation when outcomes come back to back
  b.classList.add(cls);
  setTimeout(() => b.classList.remove(cls), 450);
}

function buzz(pattern) {
  if (prefs().vibrate && navigator.vibrate) { try { navigator.vibrate(pattern); } catch (e) { /* unsupported */ } }
}

export const feedback = {
  ok() { if (prefs().sound) tone(1040, 0, 0.08); buzz(30); flash("flash-ok"); },
  done() { if (prefs().sound) { tone(784, 0, 0.09); tone(1175, 0.1, 0.16); } buzz([30, 40, 60]); flash("flash-ok"); },
  warn() { if (prefs().sound) tone(520, 0, 0.16, "triangle"); buzz(80); flash("flash-warn"); },
  error() { if (prefs().sound) { tone(200, 0, 0.16, "square", 0.14); tone(160, 0.2, 0.22, "square", 0.14); } buzz([120, 60, 120]); flash("flash-err"); },
};
