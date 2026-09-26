import * as Schema from "./schema.js";
import * as Store from "./store.js";

/**
 * Link values that point at another record of the profile must equal that record's *name*.
 * For doctypes named by a format (Storage Type = "{warehouse}-{code}") people naturally type just
 * the code ("BULK"), which then matches nothing. This finds such values and the single record
 * they almost certainly mean.
 */

function linkFieldsOf(doctypeName) {
  const out = [];
  for (const f of Schema.doctype(doctypeName).fields) {
    if (f.fieldtype === "Link" && Schema.isInScope(f.options)) out.push({ path: [f.fieldname], target: f.options, label: f.label });
    if (f.fieldtype === "Table") {
      for (const cf of Schema.doctype(f.options).fields) {
        if (cf.fieldtype === "Link" && Schema.isInScope(cf.options)) out.push({ path: [f.fieldname, cf.fieldname], target: cf.options, label: `${f.label} → ${cf.label}` });
      }
    }
  }
  return out;
}

const namesOf = (target) => new Set(Store.getRecords(target).map((r) => Schema.computeName(target, r)).filter(Boolean));

/** The one record of `target` that `value` most plausibly means, or null. `hintWarehouse` narrows ties. */
export function suggest(target, value, hintWarehouse) {
  if (!value) return null;
  const recs = Store.getRecords(target);
  const key = Schema.doctype(target).key;
  const codeField = key.fields && key.fields[key.fields.length - 1];
  let hits = recs.filter((r) => {
    const name = Schema.computeName(target, r);
    return name !== value && (r[codeField] === value || name.endsWith(`-${value}`) || name.toLowerCase() === String(value).toLowerCase());
  });
  if (hits.length > 1 && hintWarehouse) hits = hits.filter((r) => r.warehouse === hintWarehouse);
  return hits.length === 1 ? Schema.computeName(target, hits[0]) : null;
}

/** [{doctype, id, label, field, target, value, suggestion, apply(newValue)}] for every unresolved in-profile reference. */
export function findBroken() {
  const found = [];
  const nameCache = {};
  for (const doctype of Schema.schema().in_scope) {
    const links = linkFieldsOf(doctype);
    if (!links.length) continue;
    for (const rec of Store.getRecords(doctype)) {
      for (const l of links) {
        const targets = (nameCache[l.target] ||= namesOf(l.target));
        const rows = l.path.length === 1 ? [[rec, l.path[0]]] : (rec[l.path[0]] || []).map((row) => [row, l.path[1]]);
        for (const [holder, field] of rows) {
          const value = holder[field];
          if (!value || targets.has(value)) continue;
          found.push({
            doctype, id: rec.__id, label: Schema.recordLabel(doctype, rec), field: l.label, target: l.target, value,
            suggestion: suggest(l.target, value, rec.warehouse || holder.warehouse),
            set: (v) => { holder[field] = v; },
          });
        }
      }
    }
  }
  return found;
}

/** Applies every unambiguous suggestion. Returns how many references were corrected. */
export function fixAll() {
  const fixes = findBroken().filter((b) => b.suggestion);
  if (!fixes.length) return 0;
  Store.batch(() => fixes.forEach((b) => b.set(b.suggestion)));
  return fixes.length;
}
