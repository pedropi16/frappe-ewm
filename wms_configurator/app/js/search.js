// Topbar search: jump to a step, a doctype, a field's doctype or a record of the profile. "/" or Ctrl+K focuses it.
import * as Schema from "./schema.js?v=76ce61e4c0";
import * as Store from "./store.js?v=76ce61e4c0";
import { STEPS, goToStep, openRecord } from "./wizard.js?v=76ce61e4c0";

const MAX = 14;
const stepOf = (dt) => STEPS.findIndex((s) => s.doctypes && s.doctypes.includes(dt));

function recordLabel(dt, r) {
  const keys = (Schema.doctype(dt).key && Schema.doctype(dt).key.fields) || [];
  const label = keys.map((k) => r[k]).filter(Boolean).join(" / ");
  return label || Object.entries(r).find(([k, v]) => k !== "__id" && typeof v === "string" && v)?.[1] || r.__id;
}

function search(q) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const has = (text) => { const t = String(text || "").toLowerCase(); return words.every((w) => t.includes(w)); };
  const out = [];
  STEPS.forEach((s, i) => { if (has(s.title)) out.push({ kind: "Step", title: s.title, go: () => goToStep(i) }); });
  for (const dt of Schema.schema().in_scope) {
    const i = stepOf(dt);
    if (i < 0) continue;
    if (has(dt)) out.push({ kind: "Doctype", title: dt, sub: STEPS[i].title, go: () => goToStep(i) });
  }
  for (const dt of Schema.schema().in_scope) {
    const i = stepOf(dt);
    if (i < 0) continue;
    for (const r of Store.getRecords(dt)) {
      const hay = Object.entries(r).filter(([k]) => k !== "__id").map(([, v]) => (typeof v === "object" ? "" : v)).join(" ");
      if (has(hay)) out.push({ kind: "Record", title: recordLabel(dt, r), sub: dt, go: () => openRecord(dt, r.__id) });
    }
  }
  for (const dt of Schema.schema().in_scope) {
    const i = stepOf(dt);
    if (i < 0) continue;
    for (const f of Schema.doctype(dt).fields) {
      if (has(`${f.label} ${f.fieldname}`)) out.push({ kind: "Field", title: f.label, sub: dt, go: () => goToStep(i) });
    }
  }
  return out.slice(0, MAX);
}

export function initSearch() {
  const input = document.getElementById("global-search");
  const box = document.getElementById("search-results");
  let results = [], active = 0;
  const close = () => { box.hidden = true; };
  const paint = () => {
    box.innerHTML = "";
    if (!results.length) { box.innerHTML = `<div class="search-empty">No match</div>`; box.hidden = false; return; }
    results.forEach((r, i) => {
      const row = document.createElement("div");
      row.className = "search-row" + (i === active ? " active" : "");
      row.innerHTML = `<span class="search-kind"></span><span class="search-title"></span><span class="search-sub"></span>`;
      row.children[0].textContent = r.kind; row.children[1].textContent = r.title; row.children[2].textContent = r.sub || "";
      row.addEventListener("mousedown", (e) => { e.preventDefault(); pick(i); });
      box.appendChild(row);
    });
    box.hidden = false;
  };
  const pick = (i) => { const r = results[i]; if (!r) return; close(); input.blur(); input.value = ""; r.go(); };
  input.addEventListener("input", () => { const q = input.value.trim(); if (!q) return close(); results = search(q); active = 0; paint(); });
  input.addEventListener("focus", () => { if (input.value.trim()) { results = search(input.value.trim()); paint(); } });
  input.addEventListener("blur", close);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { active = Math.min(active + 1, results.length - 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); paint(); e.preventDefault(); }
    else if (e.key === "Enter") pick(active);
    else if (e.key === "Escape") { input.value = ""; close(); input.blur(); }
  });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
    if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); input.focus(); input.select(); }
  });
}
