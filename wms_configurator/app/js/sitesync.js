import * as Schema from "./schema.js?v=c05329a55f";
import * as Store from "./store.js?v=c05329a55f";
import * as ERP from "./erp.js?v=c05329a55f";

/**
 * Round trip with the connected site: pull what exists into the profile, compare the profile
 * against the site, so existing configuration can be bulk-edited and applied back as updates.
 */

const enc = encodeURIComponent;
const isEmpty = (v) => v === undefined || v === null || v === "";

async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); }
  }));
  return out;
}

/** Site document -> profile record (only fields the configurator knows, plus the site name). */
function toRecord(doctypeName, doc) {
  const dt = Schema.doctype(doctypeName);
  const rec = { __siteName: doc.name };
  for (const f of dt.fields) {
    const v = doc[f.fieldname];
    if (f.fieldtype === "Table") {
      const child = Schema.doctype(f.options);
      rec[f.fieldname] = (v || []).map((row) => {
        const r = {};
        for (const cf of child.fields) if (!isEmpty(row[cf.fieldname])) r[cf.fieldname] = row[cf.fieldname];
        return r;
      });
    } else if (!isEmpty(v)) rec[f.fieldname] = v;
  }
  return rec;
}

/** All records of a doctype from the site, as profile records. Throws with a readable message on failure. */
export async function fetchFromSite(doctypeName) {
  const dt = Schema.doctype(doctypeName);
  if (dt.issingle) {
    const r = await ERP.api("GET", `/api/method/frappe.client.get?doctype=${enc(doctypeName)}&name=${enc(doctypeName)}`);
    if (!r.ok) throw new Error(`${doctypeName}: ${r.body?.exception || r.body?.message || "HTTP " + r.status}`);
    return [toRecord(doctypeName, r.body.message)];
  }
  const r = await ERP.api("GET", `/api/resource/${enc(doctypeName)}?fields=${enc('["*"]')}&limit_page_length=0`);
  if (!r.ok) throw new Error(`${doctypeName}: ${r.body?.exception || r.body?.message || "HTTP " + r.status}`);
  const rows = r.body.data || [];
  const hasTables = Schema.tableFields(doctypeName).length > 0;
  if (!hasTables) return rows.map((row) => toRecord(doctypeName, row));
  // list results carry no child tables - fetch each document (a few at a time)
  const docs = await mapLimit(rows, 6, async (row) => {
    const d = await ERP.api("GET", `/api/resource/${enc(doctypeName)}/${enc(row.name)}`);
    return d.ok ? d.body.data : row;
  });
  return docs.map((d) => toRecord(doctypeName, d));
}

/** How an incoming site record lines up with a profile record. */
export function matcher(doctypeName) {
  const key = Schema.doctype(doctypeName).key;
  return (incoming, list) => {
    if (key.type === "single") return list[0];
    const byName = list.find((r) => r.__siteName && r.__siteName === incoming.__siteName);
    if (byName) return byName;
    if (key.type === "none") return undefined;
    const name = Schema.computeName(doctypeName, incoming);
    return name ? list.find((r) => Schema.computeName(doctypeName, r) === name) : undefined;
  };
}

/** What pulling would do, without doing it: {records, added, updated}. */
export async function previewPull(doctypeName) {
  const records = await fetchFromSite(doctypeName);
  const match = matcher(doctypeName);
  const list = Store.getRecords(doctypeName);
  const updated = records.filter((r) => match(r, list)).length;
  return { records, added: records.length - updated, updated };
}

export function commitPull(doctypeName, records) {
  return Store.upsertMany(doctypeName, records, matcher(doctypeName));
}

const comparable = (f, v) => (f.fieldtype === "Check" ? (v ? 1 : 0) : ["Int", "Float", "Percent", "Currency"].includes(f.fieldtype) ? (isEmpty(v) ? "" : Number(v)) : isEmpty(v) ? "" : String(v));

function tableSignature(child, rows) {
  return (rows || []).map((row) => child.fields.map((cf) => `${cf.fieldname}=${comparable(cf, row[cf.fieldname])}`).join("|")).sort().join("\n");
}

/**
 * Compares the profile with the site, doctype by doctype.
 * Only fields the profile actually sets are compared, so a site-side value the profile
 * leaves blank is never reported as a change (apply doesn't clear it either).
 */
export async function compareWithSite(onProgress) {
  const report = [];
  for (const doctypeName of Schema.applyOrder()) {
    const mine = Store.getRecords(doctypeName);
    if (!mine.length) continue;
    onProgress?.(doctypeName);
    const dt = Schema.doctype(doctypeName);
    let site;
    try { site = await fetchFromSite(doctypeName); } catch (e) {
      if (dt.issingle) { report.push({ doctypeName, error: e.message }); continue; }
      report.push({ doctypeName, error: e.message }); continue;
    }
    const match = matcher(doctypeName);
    const entry = { doctypeName, added: [], changed: [], same: 0, error: null };
    for (const rec of mine) {
      const label = Schema.recordLabel(doctypeName, rec);
      const found = match(rec, site);
      if (!found) { entry.added.push(label); continue; }
      const changes = [];
      for (const f of dt.fields) {
        if (f.fieldtype === "Table") {
          const child = Schema.doctype(f.options);
          const mineRows = rec[f.fieldname] || [];
          if (mineRows.length && tableSignature(child, mineRows) !== tableSignature(child, found[f.fieldname])) {
            changes.push({ field: f.label, old: `${(found[f.fieldname] || []).length} row(s)`, now: `${mineRows.length} row(s)` });
          }
          continue;
        }
        const a = rec[f.fieldname];
        if (isEmpty(a) && f.fieldtype !== "Check") continue;
        const va = comparable(f, a), vb = comparable(f, found[f.fieldname]);
        if (va !== vb) changes.push({ field: f.label, old: isEmpty(found[f.fieldname]) ? "(empty)" : String(found[f.fieldname]), now: isEmpty(a) ? "(empty)" : String(a) });
      }
      if (changes.length) entry.changed.push({ label, changes }); else entry.same++;
    }
    entry.onlyOnSite = dt.issingle ? 0 : site.filter((s) => !mine.some((r) => match(s, [r]))).length;
    report.push(entry);
  }
  return report;
}

export async function pullAll(onProgress) {
  const results = [];
  for (const doctypeName of Schema.applyOrder()) {
    onProgress?.(doctypeName);
    try {
      const records = await fetchFromSite(doctypeName);
      const { added, updated } = commitPull(doctypeName, records);
      results.push({ doctypeName, added, updated });
    } catch (e) {
      results.push({ doctypeName, error: e.message });
    }
  }
  return results;
}
