import * as ERP from "./erp.js";
import { el } from "./render.js";
import * as Schema from "./schema.js";
import { suggest } from "./refs.js";

/**
 * Searchable dropdown for Link fields. Suggestions come from the records already in the profile
 * (`getLocal`) and, when connected, from the live site (search-as-you-type). Free text is still
 * accepted, so the configurator keeps working offline.
 *
 * getLocal(): [{ value, description }]   onChange(value)
 */
export function combobox({ target, value, placeholder, getLocal = () => [], onChange, openConnect }) {
  const input = el("input", { type: "text", value: value || "", autocomplete: "off", spellcheck: "false", role: "combobox", "aria-expanded": "false" });
  input.placeholder = placeholder || "";
  const badge = el("span", { class: "combo-badge", hidden: true });
  const chevron = el("span", { class: "combo-chevron", "aria-hidden": "true" });
  const wrap = el("span", { class: "combo" }, [input, badge, chevron]);

  let panel = null, items = [], active = -1, timer = null, seq = 0, committed = value || "";

  const close = () => { panel?.remove(); panel = null; active = -1; input.setAttribute("aria-expanded", "false"); window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  function place() {
    if (!panel) return;
    const r = input.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    panel.style.left = `${r.left}px`;
    panel.style.width = `${Math.max(r.width, 260)}px`;
    panel.style.maxHeight = `${Math.max(140, Math.min(320, Math.max(below, r.top) - 16))}px`;
    if (below < 200 && r.top > below) { panel.style.top = ""; panel.style.bottom = `${window.innerHeight - r.top + 4}px`; }
    else { panel.style.bottom = ""; panel.style.top = `${r.bottom + 4}px`; }
  }
  function commit(v) {
    committed = v;
    input.value = v;
    onChange(v);
    close();
    verify();
  }

  function paint(sections, message) {
    if (!panel) {
      panel = el("div", { class: "combo-panel", role: "listbox" });
      panel.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus so blur doesn't fire first
      document.body.appendChild(panel);
      input.setAttribute("aria-expanded", "true");
      window.addEventListener("scroll", place, true);
      window.addEventListener("resize", place);
    }
    panel.innerHTML = "";
    items = [];
    for (const sec of sections) {
      if (!sec.rows.length) continue;
      if (sections.length > 1 || sec.title) panel.appendChild(el("div", { class: "combo-sec" }, sec.title));
      for (const row of sec.rows) {
        const idx = items.length;
        const node = el("div", { class: "combo-item", role: "option", "data-i": String(idx) }, [
          el("span", { class: "combo-val" }, row.value),
          row.description ? el("span", { class: "combo-desc" }, row.description) : null,
        ]);
        node.addEventListener("click", () => commit(row.value));
        node.addEventListener("mousemove", () => setActive(idx));
        items.push({ row, node });
        panel.appendChild(node);
      }
    }
    if (message) panel.appendChild(el("div", { class: "combo-msg" }, message));
    place();
    setActive(items.length ? 0 : -1);
  }
  function setActive(i) {
    active = i;
    items.forEach((it, idx) => it.node.classList.toggle("active", idx === i));
    items[i]?.node.scrollIntoView({ block: "nearest" });
  }

  async function refresh() {
    const txt = input.value.trim();
    const mine = ++seq;
    const needle = txt.toLowerCase();
    const local = getLocal().filter((r) => !needle || r.value.toLowerCase().includes(needle) || (r.description || "").toLowerCase().includes(needle)).slice(0, 30);
    const localSec = { title: local.length ? "In this profile" : "", rows: local };
    if (!ERP.isConnected()) {
      paint([localSec], local.length ? "" : `Not connected to a site - type the ${target} code, or press Connect (top right) to search and pick from real data.`);
      return;
    }
    paint([localSec], "Searching the site…");
    const remote = await ERP.searchLink(target, txt);
    if (mine !== seq || !panel) return;
    const seen = new Set(local.map((r) => r.value));
    const site = remote.filter((r) => !seen.has(r.value));
    const none = !local.length && !site.length;
    paint([localSec, { title: "On the site", rows: site }], none ? `No ${target} matches "${txt}". You can still keep the text you typed.` : "");
  }

  async function verify() {
    badge.hidden = true;
    delete badge.dataset.suggest;
    const v = input.value.trim();
    if (!v) return;
    const mark = (cls, text, title) => { badge.hidden = false; badge.className = `combo-badge ${cls}`; badge.textContent = text; badge.title = title; };
    if (getLocal().some((r) => r.value === v)) { if (ERP.isConnected()) mark("ok", "✓", "Defined in this profile"); return; }
    // a record of this profile that the typed text almost certainly means (e.g. "BULK" for "DC1-BULK")
    const near = Schema.isInScope(target) ? suggest(target, v) : null;
    if (near) { badge.dataset.suggest = near; mark("warn", "!", `No ${target} named "${v}" - did you mean "${near}"? Click to use it.`); return; }
    if (!ERP.isConnected()) return;
    const found = await ERP.exists(target, v);
    if (input.value.trim() !== v || found === null) return;
    mark(found ? "ok" : "warn", found ? "✓" : "!", found ? `Found on ${ERP.status().label}` : `No ${target} named "${v}" on ${ERP.status().label} - check the spelling, or create it there first`);
  }
  badge.addEventListener("mousedown", (e) => { if (badge.dataset.suggest) { e.preventDefault(); commit(badge.dataset.suggest); } });

  input.addEventListener("focus", () => { input.select(); refresh(); });
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 180); });
  input.addEventListener("blur", () => {
    setTimeout(() => { close(); if (input.value !== committed) { committed = input.value; onChange(input.value); verify(); } }, 0);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (!panel) refresh(); else setActive(Math.min(items.length - 1, active + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(0, active - 1)); }
    else if (e.key === "Enter" && panel && active >= 0) { e.preventDefault(); commit(items[active].row.value); }
    else if (e.key === "Escape" && panel) { e.preventDefault(); e.stopPropagation(); close(); }
  });
  chevron.addEventListener("mousedown", (e) => { e.preventDefault(); if (panel) close(); else { input.focus(); } });

  verify();
  ERP.subscribe(() => { if (wrap.isConnected) verify(); });
  return wrap;
}
