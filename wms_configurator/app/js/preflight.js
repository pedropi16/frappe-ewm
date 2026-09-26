import * as Schema from "./schema.js";
import * as Store from "./store.js";
import * as ERP from "./erp.js";

/** Mandatory fields left empty in the profile - the site rejects those records outright. */
export function findMissingRequired() {
  const out = [];
  for (const doctype of Schema.schema().in_scope) {
    const dt = Schema.doctype(doctype);
    for (const rec of Store.getRecords(doctype)) {
      const missing = dt.fields.filter((f) => {
        if (!f.reqd || f.fieldtype === "Check") return false;
        const v = rec[f.fieldname];
        return Array.isArray(v) ? !v.length : v === undefined || v === null || v === "";
      });
      if (missing.length) out.push({ doctype, id: rec.__id, label: Schema.recordLabel(doctype, rec), fields: missing });
    }
  }
  return out;
}

/** Companies on the connected site (empty when offline). */
export async function siteCompanies() {
  return (await ERP.searchLink("Company", "", 20)).map((r) => r.value);
}

/** Sets `company` on every WMS Warehouse that has none. Returns how many were filled. */
export function fillCompany(company) {
  const ids = Store.getRecords("WMS Warehouse").filter((r) => !r.company).map((r) => r.__id);
  return Store.bulkUpdate("WMS Warehouse", ids, () => ({ company }));
}

/** When the site has exactly one company there is nothing to choose: use it. */
export async function autofillSingleCompany() {
  if (!ERP.isConnected()) return 0;
  const companies = await siteCompanies();
  return companies.length === 1 ? fillCompany(companies[0]) : 0;
}
