import { autofillSingleCompany } from "./preflight.js?v=2147aeedbd";
import * as Store from "./store.js?v=2147aeedbd";
import { el } from "./render.js?v=2147aeedbd";

export const PRESETS = [
  { id: "blank", name: "Blank", description: "Start empty and build the configuration from scratch.", file: null },
  {
    id: "simple-single-zone", name: "Simple Single-Zone Warehouse",
    description: "One warehouse, one storage type with a small generated bin range, standard stock/movement types, a fallback bin-determination rule. A good starting point for a small site or a demo.",
    file: "presets/simple-single-zone.json",
  },
  {
    id: "multi-stock-type-dc", name: "Multi-Stock-Type Distribution Center",
    description: "Extends the standard seed (stock types, movement types, process types, exception codes, number ranges) with receiving/bulk/pick/staging/ship/quality zones, generated bins, and a representative rule set.",
    file: "presets/multi-stock-type-dc.json",
  },
  {
    id: "advanced-picking-packing", name: "Advanced Picking & Packing",
    description: "Builds on the Distribution Center with two-step and pick-pack-pass picking, a weigh-and-verify pack station, VAS and quality work centers, inspection sampling, tighter count tolerances, and extra print/resource coverage.",
    file: "presets/advanced-picking-packing.json",
  },
  {
    id: "advanced-execution", name: "Advanced Execution: Kitting, Repack & Cross-Dock",
    description: "Adds a second warehouse (Flow-Through Hub) running kitting, a repack center, and a deconsolidate-then-consolidate cross-dock flow, on top of everything in Advanced Picking & Packing.",
    file: "presets/advanced-execution.json",
  },
  {
    id: "enterprise-multi-site", name: "Enterprise Multi-Site",
    description: "Adds a third warehouse (Regional DC) with yard & dock appointment rules, a multi-stop milk-run route, network and print-agent printers, and turns on hourly supervisor alerts and stock ledger archiving. The most complete, hardest-to-outgrow starting point - expects multiple companies, so warehouse-to-company assignment is left for you to fill in.",
    file: "presets/enterprise-multi-site.json",
  },
];

export function renderPresetPicker(container, onChosen) {
  container.appendChild(
    el("div", { class: "step-header" }, [
      el("h1", {}, "Start a configuration profile"),
      el("p", { class: "step-help" }, "Pick a starting point, then walk through the steps on the left. Everything is editable afterward, and can be exported as a JSON profile to share or re-apply to another site."),
    ])
  );
  const grid = el("div", { class: "preset-grid" });
  for (const p of PRESETS) {
    grid.appendChild(
      el(
        "div",
        {
          class: "preset-card",
          onclick: async () => {
            if (p.file) {
              const res = await fetch(p.file);
              const data = await res.json();
              if (!Store.getProfile().profileName) data.profileName = data.profileName || p.name;
              Store.loadProfile(data);
            } else {
              Store.resetProfile();
            }
            await autofillSingleCompany(); // presets can't know your company; a single-company site has no choice to make
            onChosen();
          },
        },
        [el("h3", {}, p.name), el("p", {}, p.description)]
      )
    );
  }
  container.appendChild(grid);
}
