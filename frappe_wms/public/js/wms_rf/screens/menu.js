import { h } from "#wms/ui/dom.js";
import { S, nav } from "#wms/app.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Card, MenuGrid } from "#wms/ui/kit.js";
import { SECTIONS, TASK_TYPE_GROUPS, draftMeta, sectionCrumb } from "#wms/screens/shared.js";

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
    const items = Object.keys(SECTIONS).map((key) => {
      const n = S.tasks.filter((t) => SECTIONS[key].items.some((i) => i.taskGroup && TASK_TYPE_GROUPS[i.taskGroup].includes(t.task_type)) && t.status !== "On Hold").length;
      return { icon: SECTIONS[key].icon, label: _(SECTIONS[key].label), run: () => nav.go(`#/s/${key}`), badges: n ? [h("span.count", n)] : null };
    });
    items.push({ icon: "\u{1F50D}", label: _("Lookup"), run: () => nav.go("#/lookup") });
    items.push({ icon: "⚙️", label: _("Device & session"), run: () => nav.go("#/session") });
    wrap.append(MenuGrid(items));
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
    const items = sec.items.map((item) => {
      const group = item.taskGroup ? S.tasks.filter((t) => TASK_TYPE_GROUPS[item.taskGroup].includes(t.task_type)) : [];
      const active = group.filter((t) => t.status !== "On Hold").length;
      const hold = group.filter((t) => t.status === "On Hold").length;
      const badges = [active ? h("span.count", active) : null, hold ? h("span.count.hold", `\u{1F512}${hold}`) : null].filter(Boolean);
      return { icon: item.icon, label: _(item.label), run: () => nav.go(item.href), badges };
    });
    return h("div", MenuGrid(items));
  },
  crumb: () => null,
};
export { sectionCrumb };
