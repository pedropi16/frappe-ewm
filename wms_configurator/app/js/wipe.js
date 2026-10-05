import * as Schema from "./schema.js?v=b1b5613c18";
import * as ERP from "./erp.js?v=b1b5613c18";

const UNRESETTABLE = new Set(["Section Break", "Column Break", "Tab Break", "Table"]);

function resultMessage(r) {
  if (r.ok) return "ok";
  const b = r.body || {};
  return b.exception || b.message || (Array.isArray(b._server_messages) ? b._server_messages.join("; ") : null) || `HTTP ${r.status}`;
}

/** Deletes every record of every in-scope doctype from the connected site - the "pull the
 * plug" counterpart to applyProfileToSite in push.js. Goes in *reverse* apply order (mirrors
 * frappe_wms/setup/wipe_config.py, the bench equivalent for a site you'd rather not expose to
 * CORS), so nothing with an outgoing Link field survives long enough to matter, and resets
 * WMS Settings (a Single - nothing to delete) instead of skipping it.
 * `onProgress(entry)` is called after every record; entry = {doctypeName,label,ok,message}. */
export async function wipeSite(onProgress) {
  if (!ERP.isConnected()) throw new Error("Not connected to a site - press Connect (top right), or open this page from the site while signed in.");
  const order = [...Schema.applyOrder()].reverse();
  const results = [];
  const emit = (doctypeName, label, r) => {
    const entry = { doctypeName, label, ok: !!r.ok, message: resultMessage(r) };
    results.push(entry);
    if (onProgress) onProgress(entry);
  };

  for (const doctypeName of order) {
    const key = Schema.doctype(doctypeName).key;

    if (key.type === "single") {
      const data = {};
      for (const f of Schema.plainFields(doctypeName)) {
        if (UNRESETTABLE.has(f.fieldtype)) continue;
        data[f.fieldname] = ["Check", "Int", "Float", "Currency", "Percent"].includes(f.fieldtype) ? 0 : null;
      }
      const r = await ERP.api("POST", "/api/method/frappe.client.set_value", { doctype: doctypeName, name: doctypeName, fieldname: data });
      emit(doctypeName, doctypeName, r);
      continue;
    }

    const listRes = await ERP.api("GET", `/api/resource/${encodeURIComponent(doctypeName)}?fields=${encodeURIComponent('["name"]')}&limit_page_length=0`);
    const names = listRes.ok && listRes.body ? (listRes.body.data || []).map((r) => r.name) : [];
    if (!listRes.ok) { emit(doctypeName, "(list)", listRes); continue; }
    for (const name of names) {
      const r = await ERP.api("DELETE", `/api/resource/${encodeURIComponent(doctypeName)}/${encodeURIComponent(name)}`);
      emit(doctypeName, name, r);
    }
  }
  return results;
}
