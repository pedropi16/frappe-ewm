import * as Schema from "./schema.js?v=c05329a55f";
import * as Store from "./store.js?v=c05329a55f";
import { el, toast } from "./render.js?v=c05329a55f";
import { openBulkEdit } from "./bulk.js?v=c05329a55f";

// Row selection survives re-renders (a save elsewhere repaints the step) but is pruned to rows that still exist.
const selections = new Map();

// Columns shown in a doctype's record list. Anything not listed here gets an automatic
// selection (see autoColumns). "@address" is a virtual aisle-rack-level-position column.
const OVERRIDES = {
  "Storage Bin": ["warehouse", "storage_type", "@address", "maximum_hus"],
  "Bin Determination Rule": ["priority", "warehouse", "activity", "strategy", "destination_storage_type"],
};

const SHOWN_TYPES = ["Link", "Select", "Data", "Int", "Float", "Percent", "Currency", "Date", "Time", "Datetime"];
const MAX_AUTO_COLUMNS = 4;

const isEmpty = (v) => v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length);

function autoColumns(doctypeName) {
  const dt = Schema.doctype(doctypeName);
  const keyFields = new Set(dt.key.fields || []);
  const candidates = dt.fields.filter((f) => SHOWN_TYPES.includes(f.fieldtype) && !keyFields.has(f.fieldname) && !f.read_only);
  const rank = (f) => {
    if (f.fieldname === "priority") return 0;
    if (f.fieldname === "warehouse" || f.fieldname === "origin_warehouse") return 1;
    if (f.fieldtype === "Select" && f.reqd) return 2;
    if (f.fieldtype === "Select") return 4;
    if (f.reqd && !/_name$/.test(f.fieldname) && f.fieldtype !== "Data") return 3;
    if (/_name$/.test(f.fieldname)) return 2.5;
    if (f.fieldtype === "Link") return 5;
    return 6;
  };
  return candidates
    .map((f, i) => ({ f, r: rank(f), i }))
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, MAX_AUTO_COLUMNS)
    .sort((a, b) => a.i - b.i)
    .map((x) => x.f.fieldname);
}

function columnsFor(doctypeName) {
  const dt = Schema.doctype(doctypeName);
  const names = OVERRIDES[doctypeName] || autoColumns(doctypeName);
  return names.map((n) => {
    if (n === "@address") return { label: "Address", virtual: (r) => ["aisle", "rack", "level", "position"].map((k) => r[k]).filter((v) => !isEmpty(v)).join("-") };
    return { label: dt.fields.find((f) => f.fieldname === n).label, field: dt.fields.find((f) => f.fieldname === n) };
  });
}

function formatValue(field, v) {
  if (isEmpty(v)) return "";
  if (field.fieldtype === "Check") return v ? "Yes" : "No";
  return String(v);
}

/** Sorts rule-style doctypes (those with a priority) so the order shown is the order they're evaluated. */
export function sortForDisplay(doctypeName, records) {
  const hasPriority = Schema.doctype(doctypeName).fields.some((f) => f.fieldname === "priority");
  if (!hasPriority) return records;
  return [...records].sort((a, b) => (Number(a.priority) || 0) - (Number(b.priority) || 0));
}

function flagChips(doctypeName, r) {
  const flags = Schema.doctype(doctypeName).fields.filter((f) => f.fieldtype === "Check" && f.fieldname !== "active");
  const on = flags.filter((f) => r[f.fieldname]);
  if (!flags.length) return null;
  if (!on.length) return el("span", { class: "hint" }, "—");
  const short = (l) => l.replace(/^(Allow|Requires?|Enforce) /, "");
  const shown = on.slice(0, 2).map((f) => el("span", { class: "chip", title: f.label }, short(f.label)));
  if (on.length > 2) shown.push(el("span", { class: "chip chip-more", title: on.slice(2).map((f) => f.label).join(", ") }, `+${on.length - 2}`));
  return el("span", { class: "chips" }, shown);
}

function detailPanel(doctypeName, r) {
  const dt = Schema.doctype(doctypeName);
  const set = [];
  const unset = [];
  for (const f of dt.fields) {
    if (f.fieldtype === "Table") {
      const rows = r[f.fieldname] || [];
      if (!rows.length) { unset.push(f.label); continue; }
      const child = Schema.doctype(f.options);
      const lines = rows.map((row) => child.fields.map((cf) => (isEmpty(row[cf.fieldname]) ? null : `${cf.label}: ${formatValue(cf, row[cf.fieldname])}`)).filter(Boolean).join(" · "));
      set.push([f.label, el("ul", { class: "detail-rows" }, lines.map((l) => el("li", {}, l)))]);
      continue;
    }
    const v = r[f.fieldname];
    if (isEmpty(v) || (f.fieldtype === "Check" && !v && f.default !== "1")) { unset.push(f.label); continue; }
    set.push([f.label, formatValue(f, v)]);
  }
  const grid = el("dl", { class: "detail-grid" }, set.map(([k, v]) => el("div", { class: "detail-item" }, [el("dt", {}, k), el("dd", {}, v)])));
  return el("div", { class: "detail-panel" }, [
    grid,
    unset.length ? el("p", { class: "hint" }, `Not set: ${unset.join(", ")}`) : null,
  ]);
}

/**
 * Record list for one doctype: name, the most telling fields as columns, on/off flags as chips,
 * an Active/Inactive pill, and a row that expands to show every value in the record.
 */
export function renderRecordTable(doctypeName, records, { onEdit, onDuplicate, onDelete }) {
  const dt = Schema.doctype(doctypeName);
  const cols = columnsFor(doctypeName);
  const hasFlags = dt.fields.some((f) => f.fieldtype === "Check" && f.fieldname !== "active");
  const hasActive = dt.fields.some((f) => f.fieldname === "active");
  const nCols = cols.length + 4 + (hasFlags ? 1 : 0) + (hasActive ? 1 : 0);

  const ordered = sortForDisplay(doctypeName, records);
  const alive = new Set(records.map((r) => r.__id));
  const selected = new Set([...(selections.get(doctypeName) || [])].filter((id) => alive.has(id)));
  selections.set(doctypeName, selected);

  const rowChecks = new Map();
  const headCheck = el("input", { type: "checkbox", title: "Select all", "aria-label": "Select all" });
  const bar = el("div", { class: "bulk-bar", hidden: true });

  function paintBar() {
    for (const [id, cb] of rowChecks) { cb.checked = selected.has(id); cb.closest("tr").classList.toggle("selected", selected.has(id)); }
    headCheck.checked = selected.size > 0 && selected.size === ordered.length;
    headCheck.indeterminate = selected.size > 0 && selected.size < ordered.length;
    bar.hidden = selected.size === 0;
    bar.innerHTML = "";
    if (!selected.size) return;
    const ids = () => [...selected];
    const act = (label, fn, cls = "") => el("button", { type: "button", class: `btn btn-small ${cls}`, onclick: fn }, label);
    bar.appendChild(el("strong", {}, `${selected.size} selected`));
    if (selected.size < ordered.length) bar.appendChild(el("button", { type: "button", class: "link-btn", onclick: () => { ordered.forEach((r) => selected.add(r.__id)); paintBar(); } }, `Select all ${ordered.length}`));
    bar.appendChild(el("span", { class: "bar-spacer" }));
    bar.appendChild(act("Edit a field…", () => openBulkEdit(doctypeName, ids()), "btn-primary"));
    if (hasActive) {
      bar.appendChild(act("Activate", () => { Store.bulkUpdate(doctypeName, ids(), (r) => (r.active === 1 ? null : { active: 1 })); toast("Activated"); }));
      bar.appendChild(act("Deactivate", () => { Store.bulkUpdate(doctypeName, ids(), (r) => (r.active === 0 ? null : { active: 0 })); toast("Deactivated"); }));
    }
    bar.appendChild(act("Copy", () => { Store.duplicateMany(doctypeName, ids()); selected.clear(); toast("Copied"); }));
    bar.appendChild(act("Delete", () => { if (confirm(`Delete ${selected.size} ${doctypeName} record(s)?`)) { const n = selected.size; Store.removeMany(doctypeName, ids()); selected.clear(); toast(`${n} deleted`); } }, "btn-danger"));
    bar.appendChild(el("button", { type: "button", class: "link-btn", onclick: () => { selected.clear(); paintBar(); } }, "Clear"));
  }
  headCheck.addEventListener("change", () => { if (headCheck.checked) ordered.forEach((r) => selected.add(r.__id)); else selected.clear(); paintBar(); });

  const head = el("tr", {}, [
    el("th", { class: "col-check" }, headCheck),
    el("th", { class: "col-expand" }, ""),
    el("th", {}, "Record"),
    ...cols.map((c) => el("th", {}, c.label)),
    hasFlags ? el("th", {}, "Flags") : null,
    hasActive ? el("th", {}, "Status") : null,
    el("th", {}, ""),
  ]);

  const body = [];
  for (const r of ordered) {
    const main = el("tr", { class: "rec-row" });
    const check = el("input", { type: "checkbox", "aria-label": "Select record" });
    check.addEventListener("change", () => { if (check.checked) selected.add(r.__id); else selected.delete(r.__id); paintBar(); });
    rowChecks.set(r.__id, check);
    const toggle = el("button", { type: "button", class: "expand-btn", "aria-expanded": "false", title: "Show every value in this record" }, "▸");
    let detail = null;
    const flip = () => {
      if (detail) { detail.remove(); detail = null; toggle.textContent = "▸"; toggle.setAttribute("aria-expanded", "false"); main.classList.remove("open"); return; }
      detail = el("tr", { class: "detail-row" }, el("td", { colspan: String(nCols) }, detailPanel(doctypeName, r)));
      main.after(detail);
      toggle.textContent = "▾"; toggle.setAttribute("aria-expanded", "true"); main.classList.add("open");
    };
    toggle.addEventListener("click", flip);
    main.appendChild(el("td", { class: "col-check" }, check));
    main.appendChild(el("td", { class: "col-expand" }, toggle));
    main.appendChild(el("td", { class: "rec-name", onclick: flip }, [Schema.recordLabel(doctypeName, r), r.__siteName ? el("span", { class: "site-dot", title: "Pulled from the site - applying updates that record" }, "●") : null]));
    for (const c of cols) {
      const raw = c.virtual ? c.virtual(r) : formatValue(c.field, r[c.field.fieldname]);
      main.appendChild(el("td", {}, raw || el("span", { class: "hint" }, "—")));
    }
    if (hasFlags) main.appendChild(el("td", {}, flagChips(doctypeName, r)));
    if (hasActive) main.appendChild(el("td", {}, el("span", { class: "pill " + (r.active === 0 ? "pill-off" : "pill-on") }, r.active === 0 ? "Inactive" : "Active")));
    main.appendChild(el("td", { class: "row-actions" }, [
      el("button", { type: "button", class: "btn btn-small btn-ghost", onclick: () => onEdit(r) }, "Edit"),
      el("button", { type: "button", class: "btn btn-small btn-ghost", title: "Duplicate", "aria-label": "Duplicate", onclick: () => onDuplicate(r) }, "Copy"),
      el("button", { type: "button", class: "btn btn-small btn-ghost btn-danger", title: "Delete", "aria-label": "Delete", onclick: () => onDelete(r) }, "✕"),
    ]));
    body.push(main);
  }
  const wrap = el("div", {}, [bar, el("div", { class: "table-scroll" }, el("table", { class: "record-table rich" }, [el("thead", {}, head), el("tbody", {}, body)]))]);
  paintBar();
  return wrap;
}
