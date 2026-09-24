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

// Fixed layout: two bands. Configuration (set up once, part of a profile) on top, the day-to-day
// documents that consume it below. Edges are derived from the schema's Link fields, so a new link
// in a doctype shows up on its own. ERPNext nodes without any edge are dropped.
const BANDS = [
  { title: "CONFIGURATION", sub: "set up once - this is what a profile contains", lanes: [
    { title: "1 · Structure", sub: "what physically exists", kind: "config", items: [
      "WMS Warehouse", "Storage Type", "Storage Section", "Storage Bin", "Bin Type", "Activity Area",
      "Work Center", "WMS Stock Type", "Handling Unit Type", "Packaging Material", "Packaging Spec" ] },
    { title: "2 · Building blocks", sub: "what a task is made of", kind: "config", items: [
      "WMS Settings", "WMS Movement Type", "Warehouse Process Type", "Storage Process",
      "Storage Type Search Sequence", "WMS Exception Code", "WMS Number Range", "WMS HU Number Pool" ] },
    { title: "3 · Rules", sub: "how the system decides", kind: "config", items: [
      "Process Determination Rule", "Bin Determination Rule", "Removal Rule",
      "Warehouse Process Type Determination Rule", "WO Creation Rule", "Inspection Rule",
      "Replenishment Rule", "Count Tolerance Group", "Cycle Count Rule", "WMS Print Determination Rule" ] },
    { title: "4 · Execution & control", sub: "who does it, how it ships", kind: "config", items: [
      "WMS Route", "Warehouse Queue", "WMS Resource Group", "WMS Resource",
      "Wave Template", "Labor Standard", "Billing Rate" ] },
    { title: "5 · ERPNext masters", sub: "already exist in ERPNext", kind: "erp", items: [
      "Company", "Warehouse", "Item", "Item Group", "Customer", "Supplier", "Print Format", "User" ] },
  ] },
  { title: "DOCUMENTS", sub: "created day to day by operators and integrations - never part of a profile", lanes: [
    { title: "6 · Inbound", sub: "goods arriving", kind: "runtime", items: [
      "Inbound Delivery", "Goods Receipt", "WMS Quality Inspection" ] },
    { title: "7 · Units & products", sub: "what moves", kind: "runtime", items: [
      "Handling Unit", "WMS Product", "WMS Product Warehouse" ] },
    { title: "8 · Execution", sub: "tasks operators confirm", kind: "runtime", items: [
      "Warehouse Request", "Warehouse Order", "Warehouse Task", "WMS Print Spool", "Consolidation Group", "Kitting Order" ] },
    { title: "9 · Outbound", sub: "goods leaving", kind: "runtime", items: [
      "Outbound Delivery", "Stock Allocation", "WMS Wave", "Packing Order", "VAS Order", "Goods Issue", "WMS Shipment" ] },
    { title: "10 · Inventory", sub: "the record of truth", kind: "runtime", items: [
      "WMS Stock Balance", "WMS Stock Ledger Entry", "WMS Physical Inventory Count" ] },
    { title: "11 · ERPNext docs", sub: "mirrored or triggering", kind: "erp", items: [
      "Purchase Order", "Sales Order", "Purchase Receipt", "Delivery Note", "Stock Entry", "Quality Inspection", "BOM" ] },
  ] },
];

const SHORT = {
  "Warehouse Process Type Determination Rule": "Process Type Determination",
  "Storage Type Search Sequence": "Storage Type Search Seq.",
  "WMS Print Determination Rule": "Print Determination Rule",
  "WMS Physical Inventory Count": "Physical Inventory Count",
};

const COL_W = 216, NODE_W = 184, NODE_H = 32, ROW_H = 44, LEFT = 14;
const BAND_HEAD = 34, LANE_HEAD = 44, BAND_GAP = 26;

/** node name -> { x, y, col, band, kind, lane } (ERPNext nodes with no edge are left out). */
function layout(hasEdge) {
  const pos = {};
  let y = 8;
  const bands = BANDS.map((band, bi) => {
    const lanes = band.lanes.map((lane) => ({ ...lane, items: lane.items.filter((n) => lane.kind !== "erp" || hasEdge(n)) }));
    const rows = Math.max(...lanes.map((l) => l.items.length));
    const top = y;
    const nodesTop = top + BAND_HEAD + LANE_HEAD;
    lanes.forEach((lane, ci) => lane.items.forEach((name, ri) => {
      pos[name] = { x: LEFT + ci * COL_W, y: nodesTop + ri * ROW_H, col: ci, band: bi, kind: lane.kind };
    }));
    const height = BAND_HEAD + LANE_HEAD + rows * ROW_H + 6;
    y = top + height + BAND_GAP;
    return { ...band, lanes, top, height };
  });
  const cols = Math.max(...BANDS.map((b) => b.lanes.length));
  return { pos, bands, width: LEFT * 2 + (cols - 1) * COL_W + NODE_W, height: y - BAND_GAP + 8 };
}

/** Edges "A points at B" from Link fields (child-table links count towards their parent). */
export function buildEdges() {
  const drawn = new Set(BANDS.flatMap((b) => b.lanes.flatMap((l) => l.items)));
  const edges = new Map();
  const add = (from, to, label) => {
    if (from === to || to === "WMS Warehouse" || !drawn.has(from) || !drawn.has(to)) return;
    const key = `${from}>${to}`;
    if (!edges.has(key)) edges.set(key, { from, to, labels: [] });
    const e = edges.get(key);
    if (!e.labels.includes(label)) e.labels.push(label);
  };
  for (const from of drawn) {
    if (Schema.schema().doctypes[from] && Schema.isInScope(from)) {
      for (const f of Schema.doctype(from).fields) {
        if (f.fieldtype === "Link") add(from, f.options, f.label);
        if (f.fieldtype === "Table") {
          for (const cf of Schema.doctype(f.options).fields) {
            if (cf.fieldtype === "Link") add(from, cf.options, `${f.label} → ${cf.label}`);
          }
        }
      }
    } else if (Schema.runtime()[from]) {
      for (const l of Schema.runtime()[from].links) add(from, l.target, l.label);
    }
  }
  return [...edges.values()];
}

function edgePath(a, b) {
  if (a.band !== b.band) {
    const down = b.y > a.y;
    const x1 = a.x + NODE_W / 2, x2 = b.x + NODE_W / 2;
    const y1 = down ? a.y + NODE_H : a.y, y2 = down ? b.y : b.y + NODE_H;
    const d = Math.max(40, Math.abs(y2 - y1) * 0.4) * (down ? 1 : -1);
    return `M${x1},${y1} C${x1},${y1 + d} ${x2},${y2 - d} ${x2},${y2}`;
  }
  const ay = a.y + NODE_H / 2, by = b.y + NODE_H / 2;
  if (a.col === b.col) {
    const bulge = 40 + Math.min(Math.abs(a.y - b.y) / ROW_H, 4) * 8;
    const x = a.x + NODE_W;
    return `M${x},${ay} C${x + bulge},${ay} ${x + bulge},${by} ${x},${by}`;
  }
  const right = b.col > a.col;
  const x1 = right ? a.x + NODE_W : a.x;
  const x2 = right ? b.x : b.x + NODE_W;
  const dx = Math.max(36, Math.abs(x2 - x1) * 0.5) * (right ? 1 : -1);
  return `M${x1},${ay} C${x1 + dx},${ay} ${x2 - dx},${by} ${x2},${by}`;
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
  const edges = buildEdges();
  const touched = new Set(edges.flatMap((e) => [e.from, e.to]));
  const { pos, bands, width: WIDTH, height: HEIGHT } = layout((n) => touched.has(n));
  let selected = null;

  const card = el("div", { class: "card map-card" });
  card.appendChild(el("p", { class: "card-desc" },
    "How everything connects. The top band is what you configure (and what a profile contains); the bottom band is what the warehouse creates day to day using that configuration. An arrow means “uses / points at”: a Storage Bin points at its Storage Type, a Warehouse Task points at its Movement Type. Every record also belongs to a WMS Warehouse (not drawn). Click a box to see its connections; double-click a configuration box to open its step."));

  const root = svg("svg", { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, class: "map-svg", role: "img", "aria-label": "How configuration doctypes connect" });
  root.appendChild(svg("defs", {}, [
    svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      svg("path", { d: "M0,1 L9,5 L0,9 z", class: "map-arrow" })),
  ]));

  bands.forEach((band) => {
    root.appendChild(svg("text", { x: LEFT, y: band.top + 16, class: "map-band-title" }, band.title));
    root.appendChild(svg("text", { x: LEFT + band.title.length * 11 + 16, y: band.top + 16, class: "map-band-sub" }, band.sub));
    band.lanes.forEach((lane, ci) => {
      const x = LEFT + ci * COL_W;
      root.appendChild(svg("rect", { x: x - 10, y: band.top + BAND_HEAD - 6, width: NODE_W + 20, height: band.height - BAND_HEAD + 6, rx: 12, class: "map-lane" }));
      root.appendChild(svg("text", { x, y: band.top + BAND_HEAD + 12, class: "map-col-title" }, lane.title));
      root.appendChild(svg("text", { x, y: band.top + BAND_HEAD + 28, class: "map-col-sub" }, lane.sub));
    });
  });

  const edgeLayer = svg("g", { class: "map-edges" });
  const nodeLayer = svg("g", { class: "map-nodes" });
  root.appendChild(edgeLayer);
  root.appendChild(nodeLayer);

  const edgeEls = edges.map((e) => {
    const g = svg("g", { class: "map-edge" }, [
      svg("path", { d: edgePath(pos[e.from], pos[e.to]), class: "map-edge-line", "marker-end": "url(#arrow)" }),
    ]);
    edgeLayer.appendChild(g);
    return { ...e, g };
  });

  const nodeEls = {};
  for (const name of Object.keys(pos)) {
    const p = pos[name];
    const g = svg("g", { class: `map-node kind-${p.kind}`, transform: `translate(${p.x},${p.y})`, tabindex: "0", role: "button" });
    g.appendChild(svg("rect", { width: NODE_W, height: NODE_H, rx: 8, class: "map-node-box" }));
    g.appendChild(svg("text", { x: 12, y: NODE_H / 2 + 4, class: "map-node-label" }, SHORT[name] || name));
    const count = svg("g", { class: "map-node-count" });
    g.appendChild(count);
    g.addEventListener("click", () => select(selected === name ? null : name));
    g.addEventListener("dblclick", () => { if (p.kind === "config") openDoctype(name); });
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
        n.count.appendChild(svg("circle", { cx: NODE_W - 2, cy: 1, r: 9, class: "map-badge" }));
        n.count.appendChild(svg("text", { x: NODE_W - 2, y: 4.5, "text-anchor": "middle", class: "map-badge-text" }, String(c)));
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
    const kind = pos[selected].kind;
    const dt = kind === "config" ? Schema.doctype(selected) : null;
    const uses = edgeEls.filter((e) => e.from === selected);
    const usedBy = edgeEls.filter((e) => e.to === selected);
    const list = (rows, key) => rows.length
      ? el("ul", { class: "map-list" }, rows.map((e) => el("li", {}, [
          el("button", { type: "button", class: "link-btn", onclick: () => select(e[key]) }, e[key]),
          el("span", { class: "hint" }, ` — ${e.labels.join(", ")}`),
        ])))
      : el("p", { class: "hint" }, "Nothing.");
    const external = dt ? [...new Set(dt.fields.filter((f) => f.fieldtype === "Link" && !touched.has(f.options) && !Schema.isInScope(f.options)).map((f) => f.options))] : [];
    const kindLabel = { config: "Configuration", runtime: "Day-to-day document", erp: "ERPNext" }[kind];
    detail.appendChild(el("h3", {}, [selected, " ", el("span", { class: `kind-tag kind-${kind}` }, kindLabel)]));
    const intro = dt ? (dt.intro || dt.description) : Schema.runtimeIntro(selected);
    if (intro) detail.appendChild(el("p", { class: "map-intro" }, intro));
    detail.appendChild(el("div", { class: "map-cols" }, [
      el("div", {}, [el("h4", {}, "Uses (points at)"), list(uses, "to"),
        external.length ? el("p", { class: "hint" }, `Also links to ERPNext: ${external.join(", ")}.`) : null]),
      el("div", {}, [el("h4", {}, "Used by"), list(usedBy, "from"),
        selected === "WMS Warehouse" ? el("p", { class: "hint" }, "Almost everything else belongs to a warehouse (not drawn).") : null]),
    ]));
    if (kind === "config") detail.appendChild(el("button", { type: "button", class: "btn btn-small btn-primary", onclick: () => openDoctype(selected) }, `Configure ${selected} →`));
    else if (kind === "runtime") detail.appendChild(el("p", { class: "hint" }, "Created while the warehouse operates - it isn't part of a profile, but everything it points at in the configuration above must exist for it to work."));
  }

  card.appendChild(el("div", { class: "map-scroll" }, root));
  card.appendChild(el("div", { class: "map-legend" }, [
    el("span", { class: "lg lg-data" }, "Has records in this profile"),
    el("span", { class: "lg lg-empty" }, "Nothing yet"),
    el("span", { class: "lg lg-runtime" }, "Day-to-day document"),
    el("span", { class: "lg lg-erp" }, "ERPNext"),
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
