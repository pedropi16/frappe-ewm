import { h } from "#wms/ui/dom.js";
import { S, nav, run, notify } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field } from "#wms/ui/kit.js";
import { href } from "#wms/core/routes.js";
import { refreshSession, sectionCrumb } from "#wms/screens/shared.js";
import { setFoundTasks } from "#wms/screens/tasks.js";

// Same System Guided / Manual split as Picking (screens/picking.js): the "Putaway" menu entry
// is a choice, never a straight line into the full open-task list.
function menu(items) {
  return h("div.menu-grid", items.map((i) => h("button.menu-btn", { type: "button", onclick: i.run }, h("span.icon", i.icon), h("span", i.label))));
}

async function autoPull() {
  const wo = await run(() => api("frappe_wms.api.warehouse_order.pull_next_warehouse_order", {}), { label: _("Finding work…") });
  if (wo === undefined) return;
  if (!wo) { notify.info(_("No work waiting right now.")); return; }
  await refreshSession();
  const task = S.tasks.find((t) => t.warehouse_order === wo);
  if (task) nav.go(href("task", task.name)); else nav.go("#/tasks/inbound");
}

export const putawayMenu = {
  id: "putaway", pattern: "putaway",
  title: () => _("Putaway"), crumb: () => sectionCrumb("inbound"), parent: () => "#/s/inbound",
  render: () => menu([
    { icon: "⚡", label: _("System Guided - get next task"), run: autoPull },
    { icon: "\u{1F50D}", label: _("Manual - scan to find"), run: () => nav.go("#/putaway-manual") },
  ]),
};

const manual = { ref: "" };
export const putawayManual = {
  id: "putaway-manual", pattern: "putaway-manual",
  title: () => _("Find putaway task"), crumb: () => sectionCrumb("inbound"), parent: () => "#/putaway",
  enter() { manual.ref = ""; },
  render: () => h("div", Section({ hint: _("Scan the Handling Unit you're putting away, or a Warehouse Order, Warehouse Request, Warehouse Task, or Queue.") },
    Field({ name: "ref", kind: "scan", label: _("Reference"), placeholder: _("Scan or type"), value: manual.ref, autofocus: true,
      onInput: (v) => { manual.ref = v; }, onCommit: (v) => find(v) }))),
  actions: () => ({ primary: { label: _("Find putaway task"), run: () => manual.ref.trim() && find(manual.ref.trim()) } }),
};

async function find(reference) {
  const tasks = await run(() => api("frappe_wms.api.inbound.find_putaway_tasks", { reference }, { read: true }), { label: _("Searching…") });
  if (tasks === undefined) return false;
  if (!tasks.length) return _("No open putaway tasks found for {0}.", [reference]);
  if (tasks.length === 1) { nav.go(href("task", tasks[0].name)); return; }
  setFoundTasks(tasks, _("{0} putaway task(s) found for {1}", [tasks.length, reference]), { title: _("Putaway tasks"), section: "inbound", parent: "#/putaway" });
  nav.go("#/tasks/found");
}
