const STORAGE_KEY = "wms-configurator-profile-v1";
const PROFILE_FORMAT_VERSION = 1;

let profile = emptyProfile();
const listeners = new Set();

function emptyProfile() {
  return { formatVersion: PROFILE_FORMAT_VERSION, profileName: "", records: {} };
}

function uid() {
  return "id-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify() {
  persist();
  for (const fn of listeners) fn(profile);
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch (e) {
    // best-effort only (private browsing / quota) - in-memory state still works
    console.warn("localStorage persist failed", e);
  }
}

/** Runs fn without notifying subscribers per change (one notify at the end). */
export function batch(fn) { const r = fn(); notify(); return r; }

export function loadFromLocalStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      profile = JSON.parse(raw);
      profile.records = profile.records || {};
    }
  } catch (e) {
    console.warn("localStorage load failed", e);
  }
  return profile;
}

export function getProfile() {
  return profile;
}

export function setProfileName(name) {
  profile.profileName = name;
  notify();
}

export function getRecords(doctypeName) {
  return profile.records[doctypeName] || [];
}

export function countRecords(doctypeName) {
  return getRecords(doctypeName).length;
}

export function addRecord(doctypeName, data) {
  if (!profile.records[doctypeName]) profile.records[doctypeName] = [];
  const record = { __id: uid(), ...data };
  profile.records[doctypeName].push(record);
  notify();
  return record;
}

export function updateRecord(doctypeName, id, data) {
  const list = profile.records[doctypeName] || [];
  const idx = list.findIndex((r) => r.__id === id);
  if (idx === -1) return;
  list[idx] = { ...list[idx], ...data, __id: id };
  notify();
}

export function removeRecord(doctypeName, id) {
  const list = profile.records[doctypeName] || [];
  profile.records[doctypeName] = list.filter((r) => r.__id !== id);
  notify();
}

export function duplicateRecord(doctypeName, id) {
  const list = profile.records[doctypeName] || [];
  const rec = list.find((r) => r.__id === id);
  if (!rec) return;
  const copy = { ...structuredClone(rec), __id: uid() };
  delete copy.__siteName; // a copy is a new record, never an update of the original
  list.push(copy);
  notify();
  return copy;
}

/** Applies `fn(record)` (returning a partial to merge, or nothing) to the given records in one change. */
export function bulkUpdate(doctypeName, ids, fn) {
  const wanted = new Set(ids);
  let changed = 0;
  profile.records[doctypeName] = (profile.records[doctypeName] || []).map((r) => {
    if (!wanted.has(r.__id)) return r;
    const patch = fn(r);
    if (!patch) return r;
    changed++;
    const next = { ...r, ...patch, __id: r.__id };
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k]; // undefined in a patch clears the field
    return next;
  });
  if (changed) notify();
  return changed;
}

export function removeMany(doctypeName, ids) {
  const gone = new Set(ids);
  profile.records[doctypeName] = (profile.records[doctypeName] || []).filter((r) => !gone.has(r.__id));
  notify();
}

export function duplicateMany(doctypeName, ids) {
  const wanted = new Set(ids);
  const list = profile.records[doctypeName] || [];
  for (const r of [...list]) if (wanted.has(r.__id)) { const c = { ...structuredClone(r), __id: uid() }; delete c.__siteName; list.push(c); }
  notify();
}

/** Adds/updates many records at once (used when pulling from the site). match(r) -> existing record or undefined. */
export function upsertMany(doctypeName, incoming, match) {
  const list = profile.records[doctypeName] || (profile.records[doctypeName] = []);
  let added = 0, updated = 0;
  for (const rec of incoming) {
    const existing = match(rec, list);
    if (existing) { Object.assign(existing, rec, { __id: existing.__id }); updated++; }
    else { list.push({ __id: uid(), ...rec }); added++; }
  }
  notify();
  return { added, updated };
}

export function replaceRecords(doctypeName, records) {
  profile.records[doctypeName] = records.map((r) => ({ __id: r.__id || uid(), ...r }));
  notify();
}

export function loadProfile(newProfile) {
  profile = {
    formatVersion: newProfile.formatVersion || PROFILE_FORMAT_VERSION,
    profileName: newProfile.profileName || "",
    records: {},
  };
  for (const [doctypeName, records] of Object.entries(newProfile.records || {})) {
    profile.records[doctypeName] = (records || []).map((r) => ({ __id: r.__id || uid(), ...r }));
  }
  notify();
}

export function resetProfile() {
  profile = emptyProfile();
  notify();
}

/** Strips internal __id fields for export */
export function serializeForExport() {
  const out = { formatVersion: PROFILE_FORMAT_VERSION, profileName: profile.profileName, records: {} };
  for (const [doctypeName, records] of Object.entries(profile.records)) {
    out.records[doctypeName] = records.map((r) => {
      const { __id, __siteName, ...rest } = r;
      // strip child-table row __ids too
      const cleaned = {};
      for (const [k, v] of Object.entries(rest)) {
        cleaned[k] = Array.isArray(v) ? v.map(({ __id, ...c }) => c) : v;
      }
      return cleaned;
    });
  }
  return out;
}
