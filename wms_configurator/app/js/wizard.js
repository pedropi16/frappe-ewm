import * as Schema from "./schema.js";
import * as Store from "./store.js";
import { renderRecordForm, el, toast } from "./render.js";
import { renderPresetPicker } from "./presets.js";
import { renderBinGenerator } from "./binpattern.js";
import { renderReviewStep } from "./review.js";
import { renderMapStep } from "./diagram.js";
import { renderRecordTable } from "./listview.js";

export const STEPS = [
  { id: "start", title: "Start", kind: "preset" },
  {
    id: "map", title: "How it fits together", kind: "map",
    help: "A map of every piece and how they link, in two bands. Top: what you configure - structure (what exists), building blocks (what a task is made of), rules (how the system decides), execution (who does it, how it ships) and the ERPNext masters they point at. Bottom: the day-to-day documents that use that configuration, and the ERPNext documents they mirror.",
  },
  {
    id: "settings", title: "WMS Settings", doctypes: ["WMS Settings"],
    help: "App-wide switches: whether WMS is the only path stock can move through, whether Storage Type mixing rules are enforced, and the default Handling Unit Type the RF app falls back to.",
  },
  {
    id: "warehouse", title: "Warehouse", doctypes: ["WMS Warehouse"],
    help: "One WMS Warehouse per ERPNext Warehouse you want WMS to manage. Default receiving/shipping/difference bins can be filled in once Storage Bins exist (next steps) — you can come back.",
  },
  {
    id: "storage-types", title: "Storage Types & Layout", doctypes: ["Storage Type", "Storage Section", "Bin Type", "Activity Area"],
    help: "Storage Types (e.g. RECEIVING, BULK, PICK, STAGING, SHIP, QUALITY, DAMAGE) decide mixing rules and whether a zone is HU-managed. Include at least one with Storage Role = Door — Routes later can't work without it.",
  },
  {
    id: "storage-bins", title: "Storage Bins", kind: "bins", doctypes: ["Storage Bin"],
    help: "Physical bins: aisle/rack/level/position, capacity limits, optional stock-type/HU-type whitelists. Use the pattern generator below for ranges instead of adding bins one by one.",
  },
  {
    id: "stock-movement", title: "Stock & Movement Types", doctypes: ["WMS Stock Type", "WMS Movement Type"],
    help: "Stock Types define availability categories (Available, Quality, Blocked, Damaged, Scrap...). Movement Types are the ledger's posting codes. The bundled presets already include a standard set — add more only if you need a new category.",
  },
  {
    id: "process-types", title: "Warehouse Process Types", doctypes: ["Warehouse Process Type"],
    help: "The activity + movement-type combinations tasks are built from (unload, putaway, pick, stage, load, ...). The seeded set usually covers an MVP.",
  },
  {
    id: "determination", title: "Determination Rules", doctypes: ["Bin Determination Rule", "Process Determination Rule", "Storage Type Search Sequence"],
    help: "Bin Determination Rule needs at least one fallback per (warehouse, activity) with no item/stock-type filters so determination never dead-ends. Process Determination Rule needs at least one fallback per (warehouse, document type).",
  },
  {
    id: "storage-process", title: "Storage Process", doctypes: ["Storage Process"],
    help: "Only needed where a document type must run more than one step (e.g. unload, then putaway) — define the ordered steps here.",
  },
  {
    id: "removal", title: "Removal Rules", doctypes: ["Removal Rule"],
    help: "Optional — no matching rule falls back to FEFO-then-FIFO. Add one per (warehouse, item/item group, stock type) where you need LIFO, Fixed Bin, or another strategy.",
  },
  {
    id: "wo-rules", title: "Process & Work Order Rules", doctypes: ["Warehouse Process Type Determination Rule", "WO Creation Rule"],
    help: "Both optional. Process Type Determination Rule re-routes an activity to a different Warehouse Process Type by item/priority/warehouse. WO Creation Rule caps tasks per Warehouse Order.",
  },
  {
    id: "inspection", title: "Inspection & Exceptions", doctypes: ["Inspection Rule", "WMS Exception Code"],
    help: "Inspection Rule auto-routes matching Goods Receipt rows to QUALITY. Exception Codes are the reasons operators can raise on a task — the presets include a standard set.",
  },
  {
    id: "replenishment", title: "Replenishment Rules", doctypes: ["Replenishment Rule"],
    help: "One per (warehouse, product, pick bin) you want auto-replenished when stock falls to or below the minimum — the hourly job does the rest.",
  },
  {
    id: "counting", title: "Counting", doctypes: ["Count Tolerance Group", "Cycle Count Rule"],
    help: "Both optional. A Tolerance Group gates whether a count variance posts immediately or holds for recount/approval. A Cycle Count Rule schedules count generation (ABC/Low Stock/Zero Stock/Putaway PI/Bin Check/Annual).",
  },
  {
    id: "numbering", title: "Numbering & Printing", doctypes: ["WMS Number Range", "WMS HU Number Pool", "WMS Print Determination Rule"],
    help: "The seeded global fallback (HU-########, SHIP-########) works out of the box — add a narrower range only where a different prefix/window is needed. Print Determination Rules route events (HU Created, Goods Issue Posted, ...) to a printer.",
  },
  {
    id: "handling-units", title: "Handling Units & Packaging", doctypes: ["Handling Unit Type", "Packaging Material", "Packaging Spec"],
    help: "Handling Unit Types (pallet, carton, ...) and, optionally, per-item Packaging Specs describing how each item is packed into them.",
  },
  {
    id: "routes", title: "Routes", doctypes: ["WMS Route"],
    help: "At least one active Route per warehouse, with a default staging bin and a default door that must be a Door-role bin from Storage Types. Without this, shipments can never reach Loaded status and Goods Issue can never post.",
  },
  {
    id: "resources", title: "Resources & Queues", doctypes: ["WMS Resource", "WMS Resource Group", "Warehouse Queue", "Work Center"],
    help: "Without at least one active Warehouse Queue per (warehouse, activity), tasks are worked directly and never get Auto-pull / the Blocked-On Hold lifecycle. Pool Resources into a Resource Group so a Queue can point at the group.",
  },
  {
    id: "advanced", title: "Advanced (optional)", doctypes: ["Wave Template", "Labor Standard", "Billing Rate"],
    help: "Wave Template schedules automatic wave generation. Labor Standard feeds the KPI dashboard's efficiency numbers. Billing Rate powers the Monitor's Billing tab. Skip any of these and that one feature has nothing to compute from.",
  },
  { id: "roles", title: "Roles", kind: "roles",
    help: "The 12 fixed WMS roles created on install. Nothing to configure per-field here — just whether the target site should have them ensured to exist." },
  {
    id: "review", title: "Review & Apply", kind: "review",
    help: "Everything above is stored only in this browser until you export or apply it. Export produces a portable JSON profile; apply pushes it straight into a connected site.",
  },
];

let activeIndex = 0;
let editing = null; // { doctypeName, id|null }

export function initWizard() {
  Store.subscribe(() => { renderStepper(); renderStep(activeIndex); });
  renderStepper();
  renderStep(activeIndex);
}

export function goToStep(idOrIndex) {
  const idx = typeof idOrIndex === "number" ? idOrIndex : STEPS.findIndex((s) => s.id === idOrIndex);
  if (idx < 0) return;
  activeIndex = idx;
  editing = null;
  renderStepper();
  renderStep(activeIndex);
  document.getElementById("step-content").scrollTop = 0;
}

function stepRecordCount(step) {
  if (!step.doctypes) return 0;
  return step.doctypes.reduce((sum, dt) => sum + Store.countRecords(dt), 0);
}

function renderStepper() {
  const nav = document.getElementById("stepper");
  nav.innerHTML = "";
  STEPS.forEach((step, i) => {
    const count = stepRecordCount(step);
    const btn = el(
      "button",
      { class: "step-item" + (i === activeIndex ? " active" : ""), onclick: () => goToStep(i) },
      [
        el("span", { class: "step-num" }, String(i).padStart(2, "0")),
        step.title,
        count ? el("span", { class: "count" }, String(count)) : null,
      ]
    );
    nav.appendChild(btn);
  });
}

function renderDoctypeCard(doctypeName) {
  const dt = Schema.doctype(doctypeName);
  const records = Store.getRecords(doctypeName);
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, dt.name));
  if (dt.description) card.appendChild(el("p", { class: "card-desc" }, dt.description));

  const isEditingThis = editing && editing.doctypeName === doctypeName;

  if (dt.issingle) {
    const record = records[0] || {};
    if (isEditingThis || true) {
      card.appendChild(
        renderRecordForm(doctypeName, record, {
          onSave: (data) => {
            if (records[0]) Store.updateRecord(doctypeName, records[0].__id, data);
            else Store.addRecord(doctypeName, data);
            toast(`${dt.name} saved`);
          },
          onCancel: () => {},
        })
      );
    }
    return card;
  }

  if (!isEditingThis) {
    if (records.length) {
      card.appendChild(
        renderRecordTable(doctypeName, records, {
          onEdit: (r) => { editing = { doctypeName, id: r.__id }; renderStep(activeIndex); },
          onDuplicate: (r) => { Store.duplicateRecord(doctypeName, r.__id); },
          onDelete: (r) => { if (confirm(`Delete this ${doctypeName} record?`)) Store.removeRecord(doctypeName, r.__id); },
        })
      );
    } else {
      card.appendChild(el("p", { class: "hint" }, "No records yet."));
    }
    card.appendChild(
      el("button", { type: "button", class: "btn btn-small", onclick: () => { editing = { doctypeName, id: null }; renderStep(activeIndex); } }, `+ Add ${dt.name}`)
    );
  } else {
    const existing = editing.id ? records.find((r) => r.__id === editing.id) : {};
    card.appendChild(
      renderRecordForm(doctypeName, existing || {}, {
        onSave: (data) => {
          const id = editing.id;
          editing = null; // clear first: the store change below re-renders the step
          if (id) Store.updateRecord(doctypeName, id, data);
          else Store.addRecord(doctypeName, data);
          toast(`${dt.name} saved`);
        },
        onCancel: () => { editing = null; renderStep(activeIndex); },
      })
    );
  }
  return card;
}

function renderRolesStep(container) {
  const ROLES = ["WMS Operator", "WMS Receiver", "WMS Picker", "WMS Packer", "WMS Loader",
    "WMS Inventory Controller", "WMS Supervisor", "WMS Process Engineer", "WMS Master Data",
    "WMS Administrator", "WMS Integration User", "WMS Auditor"];
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "WMS Roles"));
  card.appendChild(el("p", { class: "card-desc" }, "Created idempotently by frappe_wms/setup/roles.py on install, and by this profile's apply step. Assign them to users directly on the target site (Users aren't part of a portable profile)."));
  const list = el("div", {}, ROLES.map((r) => el("div", {}, "• " + r)));
  card.appendChild(list);
  container.appendChild(card);
}

export function renderStep(index) {
  activeIndex = index;
  const step = STEPS[index];
  const container = document.getElementById("step-content");
  container.innerHTML = "";
  if (step.kind !== "preset") {
    container.appendChild(
      el("div", { class: "step-header" }, [el("h1", {}, step.title), step.help ? el("p", { class: "step-help" }, step.help) : null])
    );
  }

  if (step.kind === "preset") {
    renderPresetPicker(container, () => goToStep("settings"));
    return;
  }
  if (step.kind === "map") {
    renderMapStep(container, {
      openDoctype: (name) => {
        const i = STEPS.findIndex((st) => st.doctypes && st.doctypes.includes(name));
        if (i >= 0) goToStep(i);
      },
    });
    return;
  }
  if (step.kind === "roles") {
    renderRolesStep(container);
    return;
  }
  if (step.kind === "review") {
    renderReviewStep(container);
    return;
  }
  if (step.kind === "bins") {
    container.appendChild(renderBinGenerator());
  }
  for (const doctypeName of step.doctypes) {
    container.appendChild(renderDoctypeCard(doctypeName));
  }
}
