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
  list.push(copy);
  notify();
  return copy;
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
      const { __id, ...rest } = r;
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
