import * as Schema from "./schema.js";
import * as Store from "./store.js";
import { el } from "./render.js";

function pad(n, width) {
  const s = String(n);
  return width > 0 ? s.padStart(width, "0") : s;
}

function axisValues(axis) {
  if (!axis.use) return [null];
  const start = Number(axis.start) || 1;
  const end = Number(axis.end) || start;
  const width = Number(axis.pad) || 0;
  const lo = Math.min(start, end);
  const hi = Math.max(start, end);
  const values = [];
  for (let i = lo; i <= hi; i++) values.push(pad(i, width));
  return values;
}

function axisRow(label, axis, onChange) {
  const fields = ["use", "start", "end", "pad"];
  const row = el("div", { class: "field" }, [
    el("label", {}, label),
    el("div", { style: "display:flex; gap:4px; align-items:center;" }, [
      (() => {
        const cb = el("input", { type: "checkbox" });
        cb.checked = axis.use;
        cb.title = "Include this axis";
        cb.addEventListener("change", (e) => onChange({ ...axis, use: e.target.checked }));
        return cb;
      })(),
      el("input", { type: "number", value: axis.start, placeholder: "from", style: "width:56px", onchange: (e) => onChange({ ...axis, start: e.target.value }) }),
      el("input", { type: "number", value: axis.end, placeholder: "to", style: "width:56px", onchange: (e) => onChange({ ...axis, end: e.target.value }) }),
      el("input", { type: "number", value: axis.pad, placeholder: "digits", style: "width:56px", onchange: (e) => onChange({ ...axis, pad: e.target.value }) }),
    ]),
  ]);
  return row;
}

export function renderBinGenerator() {
  const state = {
    warehouse: "", storageType: "", storageSection: "", binType: "", activityArea: "",
    separator: "-", prefix: "",
    aisle: { use: true, start: 1, end: 3, pad: 2 },
    rack: { use: true, start: 1, end: 5, pad: 2 },
    level: { use: true, start: 1, end: 4, pad: 1 },
    position: { use: false, start: 1, end: 2, pad: 1 },
    binRole: "Standard", maxHus: 1, maxWeight: "", maxVolume: "", active: 1,
  };

  const wrap = el("div", { class: "card" });
  wrap.appendChild(el("h2", {}, "Bin pattern generator"));
  wrap.appendChild(el("p", { class: "card-desc" }, "Generate a range of Storage Bins at once instead of adding them one by one. Generated bins land in the table below and can still be hand-edited or deleted individually."));

  const form = el("div", { class: "form-grid" });
  const linkField = (label, key, target) => {
    const input = document.createElement("input");
    input.value = state[key];
    input.placeholder = target ? `${target} code` : "";
    const listId = "dl-bin-" + key;
    input.setAttribute("list", listId);
    input.addEventListener("change", (e) => (state[key] = e.target.value));
    const datalist = el("datalist", { id: listId }, Store.getRecords(target).map((r) => el("option", { value: Schema.computeName(target, r) })));
    return el("div", { class: "field" }, [el("label", {}, label), input, datalist]);
  };

  form.appendChild(linkField("Warehouse", "warehouse", "WMS Warehouse"));
  form.appendChild(linkField("Storage Type", "storageType", "Storage Type"));
  form.appendChild(linkField("Storage Section (optional)", "storageSection", "Storage Section"));
  form.appendChild(linkField("Bin Type (optional)", "binType", "Bin Type"));
  form.appendChild(linkField("Activity Area (optional)", "activityArea", "Activity Area"));

  form.appendChild(
    el("div", { class: "field" }, [
      el("label", {}, "Bin code prefix (optional)"),
      el("input", { type: "text", value: state.prefix, onchange: (e) => (state.prefix = e.target.value) }),
    ])
  );
  form.appendChild(
    el("div", { class: "field" }, [
      el("label", {}, "Separator"),
      el("input", { type: "text", value: state.separator, style: "width:40px", onchange: (e) => (state.separator = e.target.value) }),
    ])
  );

  const genGrid = el("div", { class: "bin-generator" });
  genGrid.appendChild(axisRow("Aisle (from / to / digits)", state.aisle, (v) => { state.aisle = v; rerenderPreview(); }));
  genGrid.appendChild(axisRow("Rack (from / to / digits)", state.rack, (v) => { state.rack = v; rerenderPreview(); }));
  genGrid.appendChild(axisRow("Level (from / to / digits)", state.level, (v) => { state.level = v; rerenderPreview(); }));
  genGrid.appendChild(axisRow("Position (from / to / digits)", state.position, (v) => { state.position = v; rerenderPreview(); }));

  form.appendChild(
    el("div", { class: "field" }, [
      el("label", {}, "Bin role"),
      (() => {
        const sel = document.createElement("select");
        const binRoleField = Schema.doctype("Storage Bin").fields.find((f) => f.fieldname === "bin_role");
        Schema.fieldOptions(binRoleField).filter(Boolean).forEach((v) => {
          const o = document.createElement("option");
          o.value = v; o.textContent = v; if (v === state.binRole) o.selected = true;
          sel.appendChild(o);
        });
        sel.addEventListener("change", (e) => (state.binRole = e.target.value));
        return sel;
      })(),
    ])
  );
  form.appendChild(el("div", { class: "field" }, [el("label", {}, "Maximum HUs"), el("input", { type: "number", value: state.maxHus, onchange: (e) => (state.maxHus = e.target.value) })]));
  form.appendChild(el("div", { class: "field" }, [el("label", {}, "Maximum weight"), el("input", { type: "number", value: state.maxWeight, onchange: (e) => (state.maxWeight = e.target.value) })]));
  form.appendChild(el("div", { class: "field" }, [el("label", {}, "Maximum volume"), el("input", { type: "number", value: state.maxVolume, onchange: (e) => (state.maxVolume = e.target.value) })]));

  wrap.appendChild(form);
  wrap.appendChild(genGrid);

  const preview = el("p", { class: "bin-preview" }, "");
  wrap.appendChild(preview);

  function count() {
    return [state.aisle, state.rack, state.level, state.position]
      .map((a) => axisValues(a).length)
      .reduce((a, b) => a * b, 1);
  }

  function rerenderPreview() {
    const n = count();
    preview.textContent = n > 2000
      ? `This will generate ${n} bins - that's a lot for one batch. Consider narrowing the ranges.`
      : `This will generate ${n} bin${n === 1 ? "" : "s"}.`;
  }
  rerenderPreview();

  const genBtn = el("button", {
    type: "button", class: "btn btn-primary",
    onclick: () => {
      if (!state.warehouse || !state.storageType) {
        alert("Set Warehouse and Storage Type first.");
        return;
      }
      const aisles = axisValues(state.aisle);
      const racks = axisValues(state.rack);
      const levels = axisValues(state.level);
      const positions = axisValues(state.position);
      const n = aisles.length * racks.length * levels.length * positions.length;
      if (n > 5000 && !confirm(`About to generate ${n} bins - continue?`)) return;
      const generated = [];
      for (const a of aisles) for (const r of racks) for (const l of levels) for (const p of positions) {
        const parts = [state.prefix, a, r, l, p].filter((v) => v !== null && v !== "");
        const bin_code = parts.join(state.separator);
        generated.push({
          bin_code, bin_name: bin_code,
          warehouse: state.warehouse, storage_type: state.storageType,
          storage_section: state.storageSection || undefined,
          bin_type: state.binType || undefined,
          activity_area: state.activityArea || undefined,
          bin_role: state.binRole,
          aisle: a || undefined, rack: r || undefined, level: l || undefined, position: p || undefined,
          maximum_hus: state.maxHus || undefined,
          maximum_weight: state.maxWeight || undefined,
          maximum_volume: state.maxVolume || undefined,
          active: 1,
        });
      }
      const existing = Store.getRecords("Storage Bin");
      const existingCodes = new Set(existing.map((r) => r.bin_code));
      let added = 0, skipped = 0;
      for (const rec of generated) {
        if (existingCodes.has(rec.bin_code)) { skipped++; continue; }
        Store.addRecord("Storage Bin", rec);
        existingCodes.add(rec.bin_code);
        added++;
      }
      alert(`Added ${added} bins${skipped ? `, skipped ${skipped} that already existed` : ""}.`);
    },
  }, "Generate bins");
  wrap.appendChild(genBtn);

  return wrap;
}
