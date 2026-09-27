import { h } from "#wms/ui/dom.js";
import { S, nav } from "#wms/app.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Card } from "#wms/ui/kit.js";
import { SECTIONS, TASK_TYPE_GROUPS, draftMeta, sectionCrumb } from "#wms/screens/shared.js";

function tile(icon, label, onClick, badges) {
  return h("button.menu-btn", { type: "button", onclick: onClick }, h("span.icon", icon), h("span", _(label)), badges && badges.length ? h("div", badges) : null);
}

export const menu = {
  id: "menu", pattern: "", root: true,
  title: () => _("WMS Scanner"),
  render() {
    const wrap = h("div");
    const drafts = draftMeta();
    if (drafts.length) {
      wrap.append(Section({ title: _("Unfinished work") },
        drafts.map((d) => Card({ title: d.label, meta: _("Tap to continue where you left off"), onClick: () => nav.go(d.route), right: h("span.badge.High", _("Draft")) }))));
    }
    const grid = h("div.menu-grid");
    for (const key of Object.keys(SECTIONS)) {
      const n = S.tasks.filter((t) => SECTIONS[key].items.some((i) => i.taskGroup && TASK_TYPE_GROUPS[i.taskGroup].includes(t.task_type)) && t.status !== "On Hold").length;
      grid.append(tile(SECTIONS[key].icon, SECTIONS[key].label, () => nav.go(`#/s/${key}`), n ? [h("span.count", n)] : null));
    }
    grid.append(tile("\u{1F50D}", "Lookup", () => nav.go("#/lookup")));
    grid.append(tile("⚙️", "Device & session", () => nav.go("#/session")));
    wrap.append(grid);
    return wrap;
  },
  async refresh() { const { refreshSession } = await import("#wms/screens/shared.js"); try { await refreshSession(); } catch (e) { /* stale menu is fine */ } },
};

export const section = {
  id: "section", pattern: "s/:key",
  title: (ctx) => { const s = SECTIONS[ctx.params.key]; return s ? _(s.label) : _("WMS Scanner"); },
  parent: () => "#/",
  enter(ctx) { if (!SECTIONS[ctx.params.key]) return { redirect: "#/" }; },
  render(ctx) {
    const sec = SECTIONS[ctx.params.key];
    const grid = h("div.menu-grid");
    for (const item of sec.items) {
      const group = item.taskGroup ? S.tasks.filter((t) => TASK_TYPE_GROUPS[item.taskGroup].includes(t.task_type)) : [];
      const active = group.filter((t) => t.status !== "On Hold").length;
      const hold = group.filter((t) => t.status === "On Hold").length;
      const badges = [active ? h("span.count", active) : null, hold ? h("span.count.hold", `\u{1F512}${hold}`) : null].filter(Boolean);
      grid.append(tile(item.icon, item.label, () => nav.go(item.href), badges));
    }
    return h("div", grid);
  },
  crumb: () => null,
};
export { sectionCrumb };
