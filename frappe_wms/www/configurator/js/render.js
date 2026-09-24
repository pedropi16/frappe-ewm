import * as Schema from "./schema.js";
import * as Store from "./store.js";

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

let datalistCounter = 0;

function linkInput(field, value, onChange) {
  const target = field.options;
  const inScope = target && Schema.isInScope(target);
  const wrap = document.createDocumentFragment();
  const listId = `dl-${datalistCounter++}`;
  const input = el("input", {
    type: "text",
    value: value || "",
    list: inScope ? listId : undefined,
    placeholder: inScope ? undefined : target ? `${target} code` : "",
    onchange: (e) => onChange(e.target.value),
  });
  wrap.appendChild(input);
  if (inScope) {
    const options = Store.getRecords(target).map((r) => Schema.computeName(target, r) || Schema.recordLabel(target, r));
    wrap.appendChild(
      el(
        "datalist",
        { id: listId },
        options.map((o) => el("option", { value: o }))
      )
    );
  }
  return wrap;
}

function fieldInput(doctypeName, field, value, onChange) {
  switch (field.fieldtype) {
    case "Check":
      return el("input", { type: "checkbox", checked: !!value, onchange: (e) => onChange(e.target.checked ? 1 : 0) });
    case "Select": {
      const opts = Schema.fieldOptions(field);
      return el(
        "select",
        { onchange: (e) => onChange(e.target.value) },
        opts.map((o) => el("option", { value: o, selected: o === (value || "") }, o || "—"))
      );
    }
    case "Int":
      return el("input", { type: "number", step: "1", value: value ?? "", onchange: (e) => onChange(e.target.value === "" ? null : parseInt(e.target.value, 10)) });
    case "Float":
    case "Currency":
    case "Percent":
      return el("input", { type: "number", step: "any", value: value ?? "", onchange: (e) => onChange(e.target.value === "" ? null : parseFloat(e.target.value)) });
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
  const thead = el("tr", {}, [...fields.map((f) => el("th", {}, f.label)), el("th", {}, "")]);
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

  const form = el("form", {
    class: "form-grid",
    onsubmit: (e) => { e.preventDefault(); onSave(draft); },
  });

  for (const f of plain) {
    if (f.fieldtype === "Check") {
      const input = fieldInput(doctypeName, f, draft[f.fieldname], (v) => (draft[f.fieldname] = v));
      form.appendChild(el("div", { class: "field checkbox" }, [input, el("label", {}, f.label)]));
      continue;
    }
    const label = el("label", {}, [f.label, f.reqd ? el("span", { class: "req" }, " *") : null]);
    const input = fieldInput(doctypeName, f, draft[f.fieldname], (v) => (draft[f.fieldname] = v));
    const fieldWrap = el("div", { class: "field" }, [label, input]);
    if (f.description) fieldWrap.title = f.description;
    form.appendChild(fieldWrap);
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
