import * as Schema from "./schema.js";
import * as Store from "./store.js";
import * as ERP from "./erp.js";

function cleanRecord(record) {
  const { __id, __siteName, ...rest } = record;
  const cleaned = {};
  for (const [k, v] of Object.entries(rest)) {
    cleaned[k] = Array.isArray(v) ? v.map(({ __id, ...c }) => c) : v;
  }
  return cleaned;
}

/** Self-referencing Link fields (e.g. Storage Bin.parent_bin) must be created
 * before the records that point at them. Best-effort stable sort, not a full
 * cycle-safe topo sort - good enough for the shallow trees this app produces. */
function selfReferentialSort(doctypeName, records) {
  const selfLinkFields = Schema.doctype(doctypeName).fields
    .filter((f) => f.fieldtype === "Link" && f.options === doctypeName)
    .map((f) => f.fieldname);
  if (!selfLinkFields.length) return records;

  const byName = new Map(records.map((r) => [Schema.computeName(doctypeName, r), r]));
  const placed = new Set();
  const ordered = [];
  let remaining = [...records];
  for (let pass = 0; pass < records.length + 1 && remaining.length; pass++) {
    const [ready, blocked] = [[], []];
    for (const r of remaining) {
      const deps = selfLinkFields.map((f) => r[f]).filter((v) => v && byName.has(v));
      if (deps.every((d) => placed.has(d))) ready.push(r);
      else blocked.push(r);
    }
    if (!ready.length) { ordered.push(...blocked); break; } // cycle guard
    ready.forEach((r) => placed.add(Schema.computeName(doctypeName, r)));
    ordered.push(...ready);
    remaining = blocked;
  }
  return ordered;
}

async function apiFetch(_baseUrl, path, opts) {
  const body = opts.body ? JSON.parse(opts.body) : undefined;
  return ERP.api(opts.method || "GET", path, body);
}

function resultMessage(r) {
  if (r.skipped) return "already present";
  if (r.ok) return "ok";
  const b = r.body || {};
  return b.exception || b.message || (Array.isArray(b._server_messages) ? b._server_messages.join("; ") : null) || `HTTP ${r.status}`;
}

/** Applies the current profile to the connected site, in dependency order.
 * `onProgress(entry)` is called after every record; entry = {doctypeName,label,ok,skipped,message}. */
export async function applyProfileToSite(onProgress) {
  if (!ERP.isConnected()) throw new Error("Not connected to a site - press Connect (top right), or open this page from the site while signed in.");
  const baseUrl = "";
  const conn = null;
  const order = Schema.applyOrder();
  const profile = Store.getProfile();
  const results = [];
  const noneKeyCache = {};

  const emit = (doctypeName, label, r) => {
    const entry = { doctypeName, label, ok: !!r.ok, skipped: !!r.skipped, message: resultMessage(r) };
    results.push(entry);
    if (onProgress) onProgress(entry);
  };

  for (const doctypeName of order) {
    let records = profile.records[doctypeName] || [];
    if (!records.length) continue;
    records = selfReferentialSort(doctypeName, records);
    const key = Schema.doctype(doctypeName).key;

    if (key.type === "single") {
      const data = cleanRecord(records[0]);
      const r = await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}`, { method: "PUT", body: JSON.stringify(data) }, conn);
      emit(doctypeName, doctypeName, r);
      continue;
    }

    if (key.type === "none") {
      if (!noneKeyCache[doctypeName]) {
        const r = await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}?fields=${encodeURIComponent('["*"]')}&limit_page_length=0`, { method: "GET" }, conn);
        noneKeyCache[doctypeName] = r.ok && r.body ? r.body.data || [] : [];
      }
      const plainFieldNames = Schema.plainFields(doctypeName).map((f) => f.fieldname);
      for (const record of records) {
        const data = cleanRecord(record);
        if (record.__siteName) {
          const r = await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}/${encodeURIComponent(record.__siteName)}`, { method: "PUT", body: JSON.stringify(data) }, conn);
          emit(doctypeName, Schema.recordLabel(doctypeName, record), r);
          continue;
        }
        const isDuplicate = noneKeyCache[doctypeName].some((existing) =>
          plainFieldNames.every((f) => String(existing[f] ?? "") === String(data[f] ?? ""))
        );
        if (isDuplicate) { emit(doctypeName, Schema.recordLabel(doctypeName, record), { ok: true, skipped: true }); continue; }
        const r = await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}`, { method: "POST", body: JSON.stringify(data) }, conn);
        emit(doctypeName, Schema.recordLabel(doctypeName, record), r);
      }
      continue;
    }

    for (const record of records) {
      const name = Schema.computeName(doctypeName, record);
      const data = cleanRecord(record);
      const getRes = await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}/${encodeURIComponent(name)}`, { method: "GET" }, conn);
      const r = getRes.ok
        ? await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(data) }, conn)
        : await apiFetch(baseUrl, `/api/resource/${encodeURIComponent(doctypeName)}`, { method: "POST", body: JSON.stringify(data) }, conn);
      emit(doctypeName, name || Schema.recordLabel(doctypeName, record), r);
    }
  }
  return results;
}
