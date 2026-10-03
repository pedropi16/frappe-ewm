import { h } from "#wms/ui/dom.js";
import { nav, run } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Btn, MenuGrid } from "#wms/ui/kit.js";
import { href } from "#wms/core/routes.js";
import { sectionCrumb, pullWorkAndOpen } from "#wms/screens/shared.js";
import { setFoundTasks } from "#wms/screens/tasks.js";

export const pickingMenu = {
  id: "picking", pattern: "picking",
  title: () => _("Picking"), crumb: () => sectionCrumb("outbound"), parent: () => "#/s/outbound",
  render: () => MenuGrid([{ icon: "⚡", label: _("Auto - get next task"), run: () => pullWorkAndOpen("#/tasks/outbound") }, { icon: "\u{1F50D}", label: _("Manual - search"), run: () => nav.go("#/picking-manual") }]),
};

const KINDS = () => [
  { icon: "\u{1F3F7}️", key: "hu", label: _("By Handling Unit"), field: _("Handling Unit"), ph: _("Scan an HU barcode") },
  { icon: "\u{1F4CB}", key: "task", label: _("By Warehouse Task"), field: _("Warehouse Task"), ph: _("Scan or type a Warehouse Task") },
  { icon: "\u{1F4E6}", key: "order", label: _("By Warehouse Order"), field: _("Warehouse Order"), ph: _("Scan or type a Warehouse Order") },
  { icon: "\u{1F4C4}", key: "request", label: _("By Warehouse Request"), field: _("Warehouse Request"), ph: _("Scan or type a Warehouse Request") },
  { icon: "\u{1F4E1}", key: "queue", label: _("By Queue"), field: _("Queue"), ph: _("Scan or type a Queue") },
  { icon: "\u{1F69A}", key: "delivery", label: _("By Outbound Delivery"), field: _("Outbound Delivery"), ph: _("Scan or type an Outbound Delivery") },
];

// One scan field for every entry point - the server tells a Warehouse Order from a delivery, a
// queue, a task or an HU by itself (services/picking.find_pick_tasks), so there is no "search by"
// choice to make first. The per-kind screens stay reachable for typed searches.
const manual = { ref: "" };
export const pickingManual = {
  id: "picking-manual", pattern: "picking-manual",
  title: () => _("Find pick task"), crumb: () => sectionCrumb("outbound"), parent: () => "#/picking",
  enter() { manual.ref = ""; },
  render: () => h("div", Section({ hint: _("Scan a Warehouse Order, Outbound Delivery, Warehouse Task, Queue or Handling Unit.") },
      Field({ name: "ref", kind: "scan", label: _("Reference"), placeholder: _("Scan or type"), value: manual.ref, autofocus: true,
        onInput: (v) => { manual.ref = v; }, onCommit: (v) => find(v) })),
    Btn({ label: _("Browse all pick tasks instead"), onClick: () => nav.go("#/tasks/outbound") }),
    h("div.hint", { style: { marginTop: "14px" } }, _("Search by a specific kind:")),
    MenuGrid(KINDS().map((k) => ({ icon: k.icon, label: k.label, run: () => nav.go(href("picking-find", k.key)) })))),
  actions: () => ({ primary: { label: _("Find pick task"), run: () => manual.ref.trim() && find(manual.ref.trim()) } }),
};

const st = { ref: "" };
export const pickingFind = {
  id: "picking-find", pattern: "picking-find/:kind",
  title: () => _("Find pick task"), crumb: () => sectionCrumb("outbound"), parent: () => "#/picking-manual",
  enter() { st.ref = ""; },
  render(ctx) {
    const k = KINDS().find((x) => x.key === ctx.params.kind) || KINDS()[0];
    return h("div", Section({ hint: _("Scan whatever is on hand to jump straight into that pick task.") },
      Field({ name: "ref", kind: "scan", label: k.field, placeholder: k.ph, value: st.ref, autofocus: true, onInput: (v) => { st.ref = v; }, onCommit: (v) => find(v) })),
      Btn({ label: _("Browse all pick tasks instead"), onClick: () => nav.go("#/tasks/outbound") }));
  },
  actions: () => ({ primary: { label: _("Find pick task"), run: () => st.ref.trim() && find(st.ref.trim()) } }),
};

async function find(reference) {
  const tasks = await run(() => api("frappe_wms.api.outbound.find_pick_tasks", { reference }, { read: true }), { label: _("Searching…") });
  if (tasks === undefined) return false;
  if (!tasks.length) return _("No open pick tasks found for {0}.", [reference]);
  if (tasks.length === 1) { nav.go(href("task", tasks[0].name)); return; }
  setFoundTasks(tasks, _("{0} pick task(s) found for {1}", [tasks.length, reference]));
  nav.go("#/tasks/found");
}
