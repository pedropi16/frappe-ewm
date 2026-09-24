let _schema = null;
let _applyOrder = null;

export async function loadSchema() {
  if (_schema) return _schema;
  const [schemaRes, orderRes] = await Promise.all([
    fetch("schema.json"),
    fetch("apply_order.json"),
  ]);
  _schema = await schemaRes.json();
  _applyOrder = await orderRes.json();
  return _schema;
}

export function schema() {
  if (!_schema) throw new Error("schema not loaded yet");
  return _schema;
}

export function applyOrder() {
  if (!_applyOrder) throw new Error("schema not loaded yet");
  return _applyOrder;
}

export function doctype(name) {
  const d = schema().doctypes[name];
  if (!d) throw new Error(`unknown doctype ${name}`);
  return d;
}

export function isInScope(name) {
  return schema().in_scope.includes(name);
}

/** Non-structural, editable fields (skips Table - rendered separately). */
export function plainFields(doctypeName) {
  return doctype(doctypeName).fields.filter((f) => f.fieldtype !== "Table");
}

export function tableFields(doctypeName) {
  return doctype(doctypeName).fields.filter((f) => f.fieldtype === "Table");
}

/** Best-effort human label for a record, based on its key strategy. */
export function recordLabel(doctypeName, record) {
  const d = doctype(doctypeName);
  const key = d.key;
  if (key.type === "field" || key.type === "format") {
    const parts = key.fields.map((f) => record[f]).filter(Boolean);
    if (parts.length) return parts.join(" / ");
  }
  // fallback: first few non-empty short text-ish fields
  const candidates = d.fields
    .filter((f) => ["Data", "Select", "Link"].includes(f.fieldtype))
    .map((f) => record[f.fieldname])
    .filter((v) => v !== undefined && v !== null && v !== "");
  if (candidates.length) return candidates.slice(0, 3).join(" / ");
  return "(unnamed)";
}

/** The document name a record will have on the Frappe side, per its autoname strategy. */
export function computeName(doctypeName, record) {
  const key = doctype(doctypeName).key;
  if (key.type === "single") return doctypeName;
  if (key.type === "field") return record[key.fields[0]] || "";
  if (key.type === "format") {
    return key.format.replace(/\{([a-zA-Z0-9_]+)\}/g, (_, f) => record[f] || "");
  }
  return ""; // "none": no stable name until the target site assigns one
}

export function fieldOptions(field) {
  // Deliberately keeps a leading blank entry (an unset optional Select) - not filtered out.
  return (field.options || "").split("\n").map((s) => s.trim());
}
