import * as Schema from "./schema.js";
import * as Store from "./store.js";
import { el } from "./render.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, children = []) => {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  for (const c of [].concat(children)) if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return n;
};

// Fixed layout: four layers, left to right. Only doctypes listed here are drawn; edges are
// derived from the schema's Link fields, so a new link in a doctype shows up on its own.
const COLUMNS = [
  { title: "1 · Structure", sub: "what physically exists", items: [
    "WMS Warehouse", "Storage Type", "Storage Section", "Storage Bin", "Bin Type", "Activity Area",
    "Work Center", "WMS Stock Type", "Handling Unit Type", "Packaging Material", "Packaging Spec" ] },
  { title: "2 · Building blocks", sub: "what a task is made of", items: [
    "WMS Settings", "WMS Movement Type", "Warehouse Process Type", "Storage Process",
    "Storage Type Search Sequence", "WMS Exception Code", "WMS Number Range", "WMS HU Number Pool" ] },
  { title: "3 · Rules", sub: "how the system decides", items: [
    "Process Determination Rule", "Bin Determination Rule", "Removal Rule",
    "Warehouse Process Type Determination Rule", "WO Creation Rule", "Inspection Rule",
    "Replenishment Rule", "Count Tolerance Group", "Cycle Count Rule", "WMS Print Determination Rule" ] },
  { title: "4 · Execution & control", sub: "who does it, and how it ships", items: [
    "WMS Route", "Warehouse Queue", "WMS Resource Group", "WMS Resource",
    "Wave Template", "Labor Standard", "Billing Rate" ] },
];

const SHORT = {
  "Warehouse Process Type Determination Rule": "Process Type Determination",
  "Storage Type Search Sequence": "Storage Type Search Seq.",
  "WMS Print Determination Rule": "Print Determination Rule",
  "Process Determination Rule": "Process Determination Rule",
};

const COL_W = 262, NODE_W = 196, NODE_H = 34, ROW_H = 52, TOP = 78, LEFT = 14;
const WIDTH = LEFT * 2 + COLUMNS.length * COL_W - (COL_W - NODE_W);
const HEIGHT = TOP + Math.max(...COLUMNS.map((c) => c.items.length)) * ROW_H + 10;

function layout() {
  const pos = {};
  COLUMNS.forEach((col, ci) => col.items.forEach((name, ri) => {
    pos[name] = { x: LEFT + ci * COL_W, y: TOP + ri * ROW_H, col: ci };
  }));
  return pos;
}

/** Edges "A points at B" from Link fields (child-table links count towards their parent). */
export function buildEdges(pos) {
  const edges = new Map();
  const add = (from, to, label) => {
    if (from === to || to === "WMS Warehouse" || !pos[from] || !pos[to]) return;
    const key = `${from}>${to}`;
    if (!edges.has(key)) edges.set(key, { from, to, labels: [] });
    const e = edges.get(key);
    if (!e.labels.includes(label)) e.labels.push(label);
  };
  for (const from of Object.keys(pos)) {
    for (const f of Schema.doctype(from).fields) {
      if (f.fieldtype === "Link") add(from, f.options, f.label);
      if (f.fieldtype === "Table") {
        for (const cf of Schema.doctype(f.options).fields) {
          if (cf.fieldtype === "Link") add(from, cf.options, `${f.label} → ${cf.label}`);
        }
      }
    }
  }
  return [...edges.values()];
}

function edgePath(a, b) {
  const ay = a.y + NODE_H / 2, by = b.y + NODE_H / 2;
  if (a.col === b.col) {
    const bulge = 46 + Math.min(Math.abs(a.y - b.y) / ROW_H, 4) * 9;
    const x = a.x + NODE_W;
    return { d: `M${x},${ay} C${x + bulge},${ay} ${x + bulge},${by} ${x},${by}`, mx: x + bulge * 0.75, my: (ay + by) / 2 };
  }
  const right = b.col > a.col;
  const x1 = right ? a.x + NODE_W : a.x;
  const x2 = right ? b.x : b.x + NODE_W;
  const dx = Math.max(40, Math.abs(x2 - x1) * 0.5) * (right ? 1 : -1);
  return { d: `M${x1},${ay} C${x1 + dx},${ay} ${x2 - dx},${by} ${x2},${by}`, mx: (x1 + x2) / 2, my: (ay + by) / 2 };
}

const CHECKS = [
  ["A WMS Warehouse exists", () => Store.countRecords("WMS Warehouse") > 0, "WMS Warehouse"],
  ["A Storage Type with role Door exists (needed to ship)", () => Store.getRecords("Storage Type").some((r) => r.storage_role === "Door"), "Storage Type"],
  ["Storage Bins exist", () => Store.countRecords("Storage Bin") > 0, "Storage Bin"],
  ["A Route with a default door exists (needed for Goods Issue)", () => Store.getRecords("WMS Route").some((r) => r.default_door), "WMS Route"],
  ["A Bin Determination Rule exists (putaway won't dead-end)", () => Store.countRecords("Bin Determination Rule") > 0, "Bin Determination Rule"],
  ["A Process Determination Rule exists", () => Store.countRecords("Process Determination Rule") > 0, "Process Determination Rule"],
  ["A Warehouse Queue exists (enables Auto-pull)", () => Store.countRecords("Warehouse Queue") > 0, "Warehouse Queue"],
];

export function renderMapStep(container, { openDoctype }) {
  const pos = layout();
  const edges = buildEdges(pos);
  let selected = null;

  const card = el("div", { class: "card map-card" });
  card.appendChild(el("p", { class: "card-desc" },
    "How the configuration pieces connect. An arrow means “uses / points at”: e.g. a Storage Bin points at its Storage Type. Every record also belongs to a WMS Warehouse (not drawn, to keep it readable). Click any box to see what it connects to; double-click to open its step."));

  const root = svg("svg", { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, class: "map-svg", role: "img", "aria-label": "How configuration doctypes connect" });
  root.appendChild(svg("defs", {}, [
    svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      svg("path", { d: "M0,1 L9,5 L0,9 z", class: "map-arrow" })),
  ]));

  COLUMNS.forEach((col, ci) => {
    const x = LEFT + ci * COL_W;
    root.appendChild(svg("rect", { x: x - 10, y: 8, width: NODE_W + 20, height: HEIGHT - 16, rx: 12, class: "map-lane" }));
    root.appendChild(svg("text", { x, y: 34, class: "map-col-title" }, col.title));
    root.appendChild(svg("text", { x, y: 52, class: "map-col-sub" }, col.sub));
  });

  const edgeLayer = svg("g", { class: "map-edges" });
  const nodeLayer = svg("g", { class: "map-nodes" });
  root.appendChild(edgeLayer);
  root.appendChild(nodeLayer);

  const edgeEls = edges.map((e) => {
    const p = edgePath(pos[e.from], pos[e.to]);
    const g = svg("g", { class: "map-edge" }, [
      svg("path", { d: p.d, class: "map-edge-line", "marker-end": "url(#arrow)" }),
    ]);
    edgeLayer.appendChild(g);
    return { ...e, g };
  });

  const nodeEls = {};
  for (const name of Object.keys(pos)) {
    const p = pos[name];
    const g = svg("g", { class: "map-node", transform: `translate(${p.x},${p.y})`, tabindex: "0", role: "button" });
    g.appendChild(svg("rect", { width: NODE_W, height: NODE_H, rx: 8, class: "map-node-box" }));
    g.appendChild(svg("text", { x: 12, y: NODE_H / 2 + 4, class: "map-node-label" }, SHORT[name] || name));
    const count = svg("g", { class: "map-node-count" });
    g.appendChild(count);
    g.addEventListener("click", () => select(selected === name ? null : name));
    g.addEventListener("dblclick", () => openDoctype(name));
    g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(selected === name ? null : name); } });
    nodeLayer.appendChild(g);
    nodeEls[name] = { g, count };
  }

  function refreshCounts() {
    for (const [name, n] of Object.entries(nodeEls)) {
      const c = Store.countRecords(name);
      n.g.classList.toggle("has-data", c > 0);
      n.count.innerHTML = "";
      if (c > 0) {
        n.count.appendChild(svg("circle", { cx: NODE_W - 16, cy: NODE_H / 2, r: 10, class: "map-badge" }));
        n.count.appendChild(svg("text", { x: NODE_W - 16, y: NODE_H / 2 + 3.5, "text-anchor": "middle", class: "map-badge-text" }, String(c)));
      }
    }
  }

  const detail = el("div", { class: "map-detail" });

  function select(name) {
    selected = name;
    for (const [n, o] of Object.entries(nodeEls)) {
      const linked = name && edges.some((e) => (e.from === name && e.to === n) || (e.to === name && e.from === n));
      o.g.classList.toggle("selected", n === name);
      o.g.classList.toggle("dim", !!name && n !== name && !linked);
    }
    for (const e of edgeEls) {
      const on = name && (e.from === name || e.to === name);
      e.g.classList.toggle("active", !!on);
      e.g.classList.toggle("dim", !!name && !on);
    }
    renderDetail();
  }

  function renderDetail() {
    detail.innerHTML = "";
    if (!selected) {
      detail.appendChild(el("p", { class: "hint" }, "Select a box to see what it uses and what uses it."));
      return;
    }
    const dt = Schema.doctype(selected);
    const uses = edgeEls.filter((e) => e.from === selected);
    const usedBy = edgeEls.filter((e) => e.to === selected);
    const list = (rows, key) => rows.length
      ? el("ul", { class: "map-list" }, rows.map((e) => el("li", {}, [
          el("button", { type: "button", class: "link-btn", onclick: () => select(e[key]) }, e[key]),
          el("span", { class: "hint" }, ` — ${e.labels.join(", ")}`),
        ])))
      : el("p", { class: "hint" }, "Nothing.");
    const external = [...new Set(dt.fields.filter((f) => f.fieldtype === "Link" && !Schema.isInScope(f.options)).map((f) => f.options))];
    detail.appendChild(el("h3", {}, selected));
    if (dt.intro || dt.description) detail.appendChild(el("p", { class: "map-intro" }, dt.intro || dt.description));
    detail.appendChild(el("div", { class: "map-cols" }, [
      el("div", {}, [el("h4", {}, "Uses (points at)"), list(uses, "to"),
        external.length ? el("p", { class: "hint" }, `Also links to ERPNext: ${external.join(", ")}.`) : null]),
      el("div", {}, [el("h4", {}, "Used by"), list(usedBy, "from"),
        selected !== "WMS Warehouse" ? el("p", { class: "hint" }, "Belongs to a WMS Warehouse.") : el("p", { class: "hint" }, "Almost everything else belongs to a warehouse.")]),
    ]));
    detail.appendChild(el("button", { type: "button", class: "btn btn-small btn-primary", onclick: () => openDoctype(selected) }, `Configure ${selected} →`));
  }

  card.appendChild(el("div", { class: "map-scroll" }, root));
  card.appendChild(el("div", { class: "map-legend" }, [
    el("span", { class: "lg lg-data" }, "Has records in this profile"),
    el("span", { class: "lg lg-empty" }, "Nothing yet"),
    el("span", { class: "lg lg-arrow" }, "→ uses / points at"),
  ]));
  card.appendChild(detail);
  container.appendChild(card);

  const checks = el("div", { class: "card" });
  checks.appendChild(el("h2", {}, "Is the minimum in place?"));
  checks.appendChild(el("p", { class: "card-desc" }, "The pieces a working warehouse can't do without. Click one to jump to where you set it up."));
  checks.appendChild(el("ul", { class: "check-list" }, CHECKS.map(([label, test, dtName]) => {
    const ok = test();
    return el("li", { class: ok ? "ok" : "todo" }, [
      el("span", { class: "check-mark" }, ok ? "✓" : "○"),
      el("button", { type: "button", class: "link-btn", onclick: () => openDoctype(dtName) }, label),
    ]);
  })));
  container.appendChild(checks);

  refreshCounts();
  renderDetail();
}
