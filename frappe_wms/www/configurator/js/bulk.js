import * as Schema from "./schema.js";
import * as Store from "./store.js";
import { el, fieldInput, toast } from "./render.js";

const TEXTY = ["Data", "Link", "Select", "Small Text", "Text Editor"];

function editableFields(doctypeName) {
  return Schema.plainFields(doctypeName).filter((f) => !f.read_only);
}

/** Modal to change one field on many records: set it to a value, or find & replace inside its text. */
export function openBulkEdit(doctypeName, ids) {
  const fields = editableFields(doctypeName);
  const records = Store.getRecords(doctypeName).filter((r) => ids.includes(r.__id));
  const keyFields = new Set(Schema.doctype(doctypeName).key.fields || []);

  let mode = "set";
  let field = fields[0];
  let value;
  let clear = false;
  let find = "", replace = "", whole = false;

  const overlay = el("div", { class: "modal" });
  const card = el("div", { class: "modal-card bulk-card" });
  overlay.appendChild(card);
  const close = () => overlay.remove();
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });

  const body = el("div", { class: "bulk-body" });
  const preview = el("div", { class: "bulk-preview" });
  const applyBtn = el("button", { type: "button", class: "btn btn-primary" }, "Apply");

  /** Returns a function record -> patch|null for the current settings. */
  function planner() {
    if (mode === "set") {
      const v = clear ? (field.fieldtype === "Check" ? 0 : "") : value;
      if (v === undefined) return null;
      return (r) => (String(r[field.fieldname] ?? "") === String(v ?? "") ? null : { [field.fieldname]: v === "" ? undefined : v });
    }
    if (!find) return null;
    return (r) => {
      const cur = r[field.fieldname];
      if (cur === undefined || cur === null || cur === "") return null;
      const s = String(cur);
      const next = whole ? (s === find ? replace : s) : s.split(find).join(replace);
      return next === s ? null : { [field.fieldname]: next };
    };
  }

  function paintPreview() {
    const plan = planner();
    preview.innerHTML = "";
    if (!plan) { preview.appendChild(el("p", { class: "hint" }, mode === "set" ? "Choose a value to see what will change." : "Type the text to find.")); applyBtn.disabled = true; return; }
    const changes = records.map((r) => ({ r, patch: plan(r) })).filter((x) => x.patch);
    applyBtn.disabled = !changes.length;
    preview.appendChild(el("p", { class: changes.length ? "" : "hint" }, `${changes.length} of ${records.length} record(s) would change.`));
    if (keyFields.has(field.fieldname) && changes.length) preview.appendChild(el("p", { class: "warn" }, "This field is part of the record's name - on the site, changed records are created as new ones and the old ones stay."));
    if (changes.length) {
      preview.appendChild(el("ul", { class: "bulk-sample" }, changes.slice(0, 6).map(({ r, patch }) => el("li", {}, [
        el("strong", {}, Schema.recordLabel(doctypeName, r)), ": ",
        String(r[field.fieldname] ?? "(empty)"), " → ", String(patch[field.fieldname] ?? "(empty)"),
      ]))));
      if (changes.length > 6) preview.appendChild(el("p", { class: "hint" }, `…and ${changes.length - 6} more`));
    }
  }

  function paintBody() {
    body.innerHTML = "";
    const modeSel = el("div", { class: "seg" }, [
      el("button", { type: "button", class: "seg-btn" + (mode === "set" ? " on" : ""), onclick: () => { mode = "set"; paintBody(); } }, "Set a value"),
      el("button", { type: "button", class: "seg-btn" + (mode === "replace" ? " on" : ""), onclick: () => {
        mode = "replace";
        if (!TEXTY.includes(field.fieldtype)) field = fields.find((f) => TEXTY.includes(f.fieldtype));
        paintBody();
      } }, "Find & replace"),
    ]);
    body.appendChild(modeSel);

    const pool = mode === "replace" ? fields.filter((f) => TEXTY.includes(f.fieldtype)) : fields;
    const select = el("select", { onchange: (e) => { field = fields.find((f) => f.fieldname === e.target.value); value = undefined; clear = false; paintBody(); } },
      pool.map((f) => el("option", { value: f.fieldname, selected: f.fieldname === field.fieldname }, f.label + (f.reqd ? " *" : ""))));
    body.appendChild(el("div", { class: "field" }, [el("label", {}, "Field"), el("span", { class: "select-wrap" }, select)]));

    if (mode === "set") {
      const holder = el("div", { class: "field" }, [el("label", {}, "New value")]);
      holder.appendChild(fieldInput(doctypeName, field, value, (v) => { value = v; clear = false; paintPreview(); }));
      body.appendChild(holder);
      if (!field.reqd && field.fieldtype !== "Check") {
        body.appendChild(el("label", { class: "inline-check" }, [
          el("input", { type: "checkbox", checked: clear, onchange: (e) => { clear = e.target.checked; paintPreview(); } }), " Clear the field instead",
        ]));
      }
    } else {
      const t = (label, get, set, ph) => {
        const input = el("input", { type: "text", value: get(), placeholder: ph });
        input.addEventListener("input", (e) => { set(e.target.value); paintPreview(); });
        return el("div", { class: "field" }, [el("label", {}, label), input]);
      };
      body.appendChild(t("Find", () => find, (v) => (find = v), "e.g. DC1-"));
      body.appendChild(t("Replace with", () => replace, (v) => (replace = v), "leave empty to remove the text"));
      body.appendChild(el("label", { class: "inline-check" }, [
        el("input", { type: "checkbox", checked: whole, onchange: (e) => { whole = e.target.checked; paintPreview(); } }), " Match the whole value only",
      ]));
    }
    paintPreview();
  }

  applyBtn.addEventListener("click", () => {
    const plan = planner();
    if (!plan) return;
    const n = Store.bulkUpdate(doctypeName, ids, (r) => plan(r));
    close();
    toast(`${n} ${doctypeName} record(s) updated`);
  });

  card.appendChild(el("h2", {}, `Edit ${records.length} ${doctypeName} record${records.length === 1 ? "" : "s"}`));
  card.appendChild(body);
  card.appendChild(preview);
  card.appendChild(el("div", { class: "modal-actions" }, [
    el("button", { type: "button", class: "btn btn-ghost", onclick: close }, "Cancel"),
    applyBtn,
  ]));
  paintBody();
  document.body.appendChild(overlay);
}
