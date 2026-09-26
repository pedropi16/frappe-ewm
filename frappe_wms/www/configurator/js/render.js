import * as Schema from "./schema.js?v=c05329a55f";
import * as Store from "./store.js?v=c05329a55f";
import { combobox } from "./combobox.js?v=c05329a55f";

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== undefined && v !== null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function linkInput(field, value, onChange) {
  const target = field.options;
  const inScope = target && Schema.isInScope(target);
  const getLocal = () => (inScope
    ? Store.getRecords(target).map((r) => {
        const name = Schema.computeName(target, r) || Schema.recordLabel(target, r);
        const label = Schema.recordLabel(target, r);
        return { value: name, description: label !== name ? label : "" };
      })
    : []);
  return combobox({
    target,
    value,
    getLocal,
    onChange,
    placeholder: `Search ${target}…`,
  });
}

function numberInput(value, isInt, onChange) {
  const parse = (v) => (v === "" ? null : isInt ? parseInt(v, 10) : parseFloat(v));
  const input = el("input", { type: "number", step: isInt ? "1" : "any", value: value ?? "", placeholder: "0", onchange: (e) => onChange(parse(e.target.value)) });
  const bump = (d) => {
    const cur = parse(input.value) || 0;
    input.value = String(Math.round((cur + d) * 1e6) / 1e6);
    onChange(parse(input.value));
  };
  return el("span", { class: "stepper-num" }, [
    el("button", { type: "button", class: "num-btn", tabindex: "-1", "aria-label": "Decrease", onclick: () => bump(-1) }, "−"),
    input,
    el("button", { type: "button", class: "num-btn", tabindex: "-1", "aria-label": "Increase", onclick: () => bump(1) }, "+"),
  ]);
}

/** Visible help text for a field: its own description, else a generated hint from type/options. */
function hintFor(f) {
  if (f.description) return f.description;
  const bits = [];
  switch (f.fieldtype) {
    case "Link":
      bits.push(Schema.isInScope(f.options)
        ? `Pick a ${f.options}. Suggestions come from the ${f.options} records in this profile (add them in their own step first).`
        : `Name of an existing ${f.options} on the target site.`);
      break;
    case "Select": {
      const o = Schema.fieldOptions(f).filter(Boolean);
      if (o.length) bits.push(`One of: ${o.slice(0, 8).join(", ")}${o.length > 8 ? ", …" : ""}.`);
      break;
    }
    case "Check": bits.push("Switch on to enable."); break;
    case "Int": bits.push("Whole number."); break;
    case "Float": case "Currency": case "Percent": bits.push("Number; decimals allowed."); break;
    case "Date": bits.push("Calendar date."); break;
    case "Datetime": bits.push("Date and time."); break;
    case "Time": bits.push("Time of day."); break;
    case "Small Text": case "Text Editor": bits.push("Free text."); break;
    default: break;
  }
  if (f.default !== undefined && f.default !== null && f.default !== "") {
    bits.push(f.fieldtype === "Check" ? `Default: ${f.default === "1" || f.default === 1 ? "on" : "off"}.` : `Default: ${f.default}.`);
  }
  return bits.join(" ");
}

function helpNode(f) {
  const text = hintFor(f);
  if (!text) return null;
  const long = text.length > 130;
  const p = el("p", { class: "field-help" + (long ? " clamped" : "") }, text);
  if (!long) return p;
  const more = el("button", { type: "button", class: "more-link", onclick: () => {
    const open = p.classList.toggle("clamped");
    more.textContent = open ? "Show more" : "Show less";
  } }, "Show more");
  return el("div", { class: "help-wrap" }, [p, more]);
}

let toastTimer = null;
export function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) { t = el("div", { id: "toast", class: "toast", role: "status" }); document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

export function fieldInput(doctypeName, field, value, onChange) {
  switch (field.fieldtype) {
    case "Check":
      return el("span", { class: "switch" }, [
        el("input", { type: "checkbox", checked: !!value, onchange: (e) => onChange(e.target.checked ? 1 : 0) }),
        el("span", { class: "switch-track" }),
      ]);
    case "Select": {
      const opts = Schema.fieldOptions(field);
      return el("span", { class: "select-wrap" }, [
        el(
          "select",
          { onchange: (e) => onChange(e.target.value) },
          opts.map((o) => el("option", { value: o, selected: o === (value || "") }, o || "— select —"))
        ),
      ]);
    }
    case "Int":
      return numberInput(value, true, onChange);
    case "Float":
    case "Currency":
    case "Percent":
      return numberInput(value, false, onChange);
    case "Date":
      return el("input", { type: "date", value: value || "", onchange: (e) => onChange(e.target.value) });
    case "Datetime":
      return el("input", { type: "datetime-local", value: value || "", onchange: (e) => onChange(e.target.value) });
    case "Time":
      return el("input", { type: "time", value: value || "", onchange: (e) => onChange(e.target.value) });
    case "Small Text":
    case "Text Editor":
      return el("textarea", { onchange: (e) => onChange(e.target.value) }, value || "");
    case "Link":
      return linkInput(field, value, onChange);
    default:
      return el("input", { type: "text", value: value ?? "", onchange: (e) => onChange(e.target.value) });
  }
}

function childTableEditor(childDoctypeName, rows, onChange) {
  const fields = Schema.plainFields(childDoctypeName);
  let data = rows.map((r) => ({ ...r }));

  const wrap = el("div", { class: "child-table-wrap" });
  wrap.appendChild(el("h3", {}, Schema.doctype(childDoctypeName).name));
  const table = el("table", { class: "child-table" });
  const thead = el("tr", {}, [...fields.map((f) => el("th", { title: hintFor(f) }, f.label)), el("th", {}, "")]);
  table.appendChild(el("thead", {}, thead));
  const tbody = el("tbody");
  table.appendChild(tbody);

  function renderRows() {
    tbody.innerHTML = "";
    data.forEach((row, i) => {
      const tr = el("tr");
      for (const f of fields) {
        const td = el("td");
        td.appendChild(fieldInput(childDoctypeName, f, row[f.fieldname], (v) => {
          data[i] = { ...data[i], [f.fieldname]: v };
          onChange(data);
        }));
        tr.appendChild(td);
      }
      tr.appendChild(
        el("td", {}, el("button", {
          type: "button",
          class: "btn btn-small btn-ghost",
          onclick: () => { data = data.filter((_, idx) => idx !== i); onChange(data); renderRows(); },
        }, "✕"))
      );
      tbody.appendChild(tr);
    });
  }
  renderRows();
  wrap.appendChild(table);
  wrap.appendChild(
    el("button", {
      type: "button",
      class: "btn btn-small",
      onclick: () => { data = [...data, {}]; onChange(data); renderRows(); },
    }, "+ Add row")
  );
  return wrap;
}

/**
 * Renders an editable form for one record of `doctypeName`.
 * `record` may be {} for a new record.
 * Calls onSave(cleanedData) / onCancel() when the user acts.
 */
export function renderRecordForm(doctypeName, record, { onSave, onCancel }) {
  let draft = { ...record };
  const plain = Schema.plainFields(doctypeName);
  const tables = Schema.tableFields(doctypeName);
  // What the form displays must be what gets saved: fill in schema defaults (and a required
  // Select's first option, which the browser shows as selected) for anything still unset.
  for (const f of plain) {
    if (draft[f.fieldname] !== undefined && draft[f.fieldname] !== null) continue;
    const dflt = f.default;
    if (dflt !== undefined && dflt !== null && dflt !== "") {
      if (f.fieldtype === "Check" || f.fieldtype === "Int") draft[f.fieldname] = parseInt(dflt, 10) || 0;
      else if (["Float", "Currency", "Percent"].includes(f.fieldtype)) { const n = parseFloat(dflt); if (!Number.isNaN(n)) draft[f.fieldname] = n; }
      else if (f.fieldtype === "Data" || f.fieldtype === "Select" || f.fieldtype === "Link") draft[f.fieldname] = dflt;
    } else if (f.fieldtype === "Select" && f.reqd) {
      const first = Schema.fieldOptions(f).find(Boolean);
      if (first) draft[f.fieldname] = first;
    }
  }

  const form = el("form", {
    class: "form-grid",
    novalidate: true,
    onsubmit: (e) => {
      e.preventDefault();
      const missing = plain.filter((f) => f.reqd && f.fieldtype !== "Check" && (draft[f.fieldname] === undefined || draft[f.fieldname] === null || draft[f.fieldname] === ""));
      form.querySelectorAll(".field.invalid").forEach((n) => n.classList.remove("invalid"));
      if (missing.length) {
        for (const f of missing) form.querySelector(`[data-field="${f.fieldname}"]`)?.classList.add("invalid");
        form.querySelector(".field.invalid")?.scrollIntoView({ block: "center", behavior: "smooth" });
        toast(`Fill in the required field${missing.length > 1 ? "s" : ""}: ${missing.map((f) => f.label).join(", ")}`);
        return;
      }
      onSave(draft);
    },
  });

  for (const f of plain) {
    const req = f.reqd ? el("span", { class: "req", title: "Required" }, " *") : null;
    if (f.fieldtype === "Check") {
      const input = fieldInput(doctypeName, f, draft[f.fieldname], (v) => (draft[f.fieldname] = v));
      form.appendChild(el("div", { class: "field checkbox", "data-field": f.fieldname }, [
        el("label", { class: "switch-row" }, [input, el("span", { class: "switch-label" }, f.label)]),
        helpNode(f),
      ]));
      continue;
    }
    const input = fieldInput(doctypeName, f, draft[f.fieldname], (v) => {
      draft[f.fieldname] = v;
      form.querySelector(`[data-field="${f.fieldname}"]`)?.classList.remove("invalid");
    });
    form.appendChild(el("div", { class: "field", "data-field": f.fieldname }, [
      el("label", {}, [f.label, req]),
      input,
      helpNode(f),
    ]));
  }

  for (const tf of tables) {
    const rows = draft[tf.fieldname] || [];
    form.appendChild(childTableEditor(tf.options, rows, (newRows) => (draft[tf.fieldname] = newRows)));
  }

  const actions = el("div", { class: "form-actions" }, [
    el("button", { type: "submit", class: "btn btn-primary" }, "Save"),
    el("button", { type: "button", class: "btn btn-ghost", onclick: () => onCancel() }, "Cancel"),
  ]);
  form.appendChild(el("div", { class: "child-table-wrap" }, actions));
  return form;
}

export { el };
