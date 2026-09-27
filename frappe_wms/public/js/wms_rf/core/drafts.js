// In-progress work (a half-done Move, scanned lines on a Goods Receipt, a task wizard) survives reload, an accidental
// Back, the OS killing the tab, and a session-expiry login round trip, because it is saved as the operator goes.
// sessionStorage is per-tab, so a fresh tab / a new shift always starts clean; the TTL guards very long-lived tabs.
const NS = "wms.rf.draft.";
const VERSION = 1;
export const DRAFT_TTL_MS = 12 * 60 * 60 * 1000;

export function createDrafts(storage, { now = Date.now, ttl = DRAFT_TTL_MS } = {}) {
  const safe = (fn, fallback) => { try { return fn(); } catch (e) { return fallback; } };
  return {
    save(key, data) {
      return safe(() => { storage.setItem(NS + key, JSON.stringify({ v: VERSION, t: now(), data })); return true; }, false);
    },
    load(key) {
      return safe(() => {
        const raw = storage.getItem(NS + key);
        if (!raw) return null;
        const entry = JSON.parse(raw);
        if (!entry || entry.v !== VERSION || now() - entry.t > ttl) { storage.removeItem(NS + key); return null; }
        return entry.data;
      }, null);
    },
    clear(key) { safe(() => storage.removeItem(NS + key)); },
    has(key) { return this.load(key) !== null; },
    keys() {
      return safe(() => {
        const out = [];
        for (let i = 0; i < storage.length; i++) {
          const k = storage.key(i);
          if (k && k.startsWith(NS)) out.push(k.slice(NS.length));
        }
        return out.filter((k) => this.load(k) !== null);
      }, []);
    },
    clearAll() { this.keys().forEach((k) => this.clear(k)); },
  };
}
