import * as Store from "./store.js";
import { el } from "./render.js";

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
            onChosen();
          },
        },
        [el("h3", {}, p.name), el("p", {}, p.description)]
      )
    );
  }
  container.appendChild(grid);
}
