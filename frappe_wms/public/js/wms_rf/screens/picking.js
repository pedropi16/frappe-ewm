import { h } from "#wms/ui/dom.js";
import { S, nav, run, notify } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Btn } from "#wms/ui/kit.js";
import { href } from "#wms/core/routes.js";
import { refreshSession, sectionCrumb } from "#wms/screens/shared.js";
import { setFoundTasks } from "#wms/screens/tasks.js";

function menu(items) {
  return h("div.menu-grid", items.map((i) => h("button.menu-btn", { type: "button", onclick: i.run }, h("span.icon", i.icon), h("span", i.label))));
}

async function autoPull() {
  const wo = await run(() => api("frappe_wms.api.warehouse_order.pull_next_warehouse_order", {}), { label: _("Finding work…") });
  if (wo === undefined) return;
  if (!wo) { notify.info(_("No work waiting right now.")); return; }
  await refreshSession();
  const task = S.tasks.find((t) => t.warehouse_order === wo);
  if (task) nav.go(href("task", task.name)); else nav.go("#/tasks/outbound");
}

export const pickingMenu = {
  id: "picking", pattern: "picking",
  title: () => _("Picking"), crumb: () => sectionCrumb("outbound"), parent: () => "#/s/outbound",
  render: () => menu([{ icon: "⚡", label: _("Auto - get next task"), run: autoPull }, { icon: "\u{1F50D}", label: _("Manual - search"), run: () => nav.go("#/picking-manual") }]),
};

const KINDS = () => [
  { icon: "\u{1F3F7}️", key: "hu", label: _("By Handling Unit"), field: _("Handling Unit"), ph: _("Scan an HU barcode") },
  { icon: "\u{1F4CB}", key: "task", label: _("By Warehouse Task"), field: _("Warehouse Task"), ph: _("Scan or type a Warehouse Task") },
  { icon: "\u{1F4E6}", key: "order", label: _("By Warehouse Order"), field: _("Warehouse Order"), ph: _("Scan or type a Warehouse Order") },
  { icon: "\u{1F4C4}", key: "request", label: _("By Warehouse Request"), field: _("Warehouse Request"), ph: _("Scan or type a Warehouse Request") },
  { icon: "\u{1F4E1}", key: "queue", label: _("By Queue"), field: _("Queue"), ph: _("Scan or type a Queue") },
  { icon: "\u{1F69A}", key: "delivery", label: _("By Outbound Delivery"), field: _("Outbound Delivery"), ph: _("Scan or type an Outbound Delivery") },
];

export const pickingManual = {
  id: "picking-manual", pattern: "picking-manual",
  title: () => _("Find pick task"), crumb: () => sectionCrumb("outbound"), parent: () => "#/picking",
  render: () => menu(KINDS().map((k) => ({ icon: k.icon, label: k.label, run: () => nav.go(href("picking-find", k.key)) }))),
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
