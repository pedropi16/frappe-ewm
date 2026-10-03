import { h } from "#wms/ui/dom.js";
import { nav, run } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field } from "#wms/ui/kit.js";
import { href } from "#wms/core/routes.js";
import { sectionCrumb } from "#wms/screens/shared.js";
import { setFoundTasks } from "#wms/screens/tasks.js";

// No auto-pull here: by the time an operator opens Putaway, the task (and its destination bin,
// via Bin Determination Rule) already exists - created server-side in the same call that posted
// the Goods Receipt. There's nothing to "get assigned"; the operator is already holding the HU,
// so the only thing this screen does is resolve what they're holding to its task.
const state = { ref: "" };
export const putawayMenu = {
  id: "putaway", pattern: "putaway",
  title: () => _("Putaway"), crumb: () => sectionCrumb("inbound"), parent: () => "#/s/inbound",
  enter() { state.ref = ""; },
  render: () => h("div", Section({ hint: _("Scan the Handling Unit you're putting away, or a Warehouse Order, Warehouse Request, Warehouse Task, or Queue.") },
    Field({ name: "ref", kind: "scan", label: _("Reference"), placeholder: _("Scan or type"), value: state.ref, autofocus: true,
      onInput: (v) => { state.ref = v; }, onCommit: (v) => find(v) }))),
  actions: () => ({ primary: { label: _("Find putaway task"), run: () => state.ref.trim() && find(state.ref.trim()) } }),
};

async function find(reference) {
  const tasks = await run(() => api("frappe_wms.api.inbound.find_putaway_tasks", { reference }, { read: true }), { label: _("Searching…") });
  if (tasks === undefined) return false;
  if (!tasks.length) return _("No open putaway tasks found for {0}.", [reference]);
  if (tasks.length === 1) { nav.go(href("task", tasks[0].name)); return; }
  setFoundTasks(tasks, _("{0} putaway task(s) found for {1}", [tasks.length, reference]), { title: _("Putaway tasks"), section: "inbound", parent: "#/putaway" });
  nav.go("#/tasks/found");
}
