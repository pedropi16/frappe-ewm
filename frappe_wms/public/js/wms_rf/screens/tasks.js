import { h } from "#wms/ui/dom.js";
import { S, nav, load, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Card, Empty, Loading, StatusBadge, Badge, Hint } from "#wms/ui/kit.js";
import { TASK_TYPE_GROUPS, SECTIONS, refreshSession, sectionCrumb, sectionHash, taskLocation } from "#wms/screens/shared.js";
import { fmtQty } from "#wms/core/util.js";
import { href } from "#wms/core/routes.js";

const st = { loading: false, loaded: false, found: [], foundLabel: "" };

export function groupOfType(taskType) {
  return Object.keys(TASK_TYPE_GROUPS).find((g) => TASK_TYPE_GROUPS[g].includes(taskType)) || "inbound";
}

export function setFoundTasks(tasks, label) { st.found = tasks; st.foundLabel = label; }

async function refresh() {
  st.loading = true; update();
  await load(refreshSession);
  st.loading = false; st.loaded = true; update();
}

async function pullWork() {
  // run() returns undefined for two different reasons: its own exclusive-busy guard never called
  // fn() at all (no notice shown), or fn() ran and genuinely resolved to undefined - which is
  // exactly what happens here, because pull_next_warehouse_order() returning Python's None comes
  // back as a response body with no "message" key at all (confirmed live: a raw request to it
  // returns literally "{}"), and api()'s once() just returns data.message, i.e. undefined. Treating
  // every undefined as "didn't run" meant the ordinary, extremely common "nothing to pull right
  // now" case showed no notice, no navigation - nothing. An operator tapping "Get next work" with
  // an empty queue saw the button do nothing and had no way to tell that from it being broken.
  const wo = await run(() => api("frappe_wms.api.warehouse_order.pull_next_warehouse_order", {}), { label: _("Finding work…") });
  if (!wo) { notify.info(_("No work waiting right now.")); return; }
  await refreshSession();
  let task = S.tasks.find((t) => t.warehouse_order === wo);
  if (!task) {
    // "my tasks" is a capped, warehouse-wide view (list_my_tasks), not a per-Warehouse-Order one -
    // a resource that has personally accumulated a large backlog of its own earlier open/on-hold
    // work can rank that ahead of a task from a Warehouse Order genuinely just assigned to it this
    // instant, so it's a real possibility this WO's own task isn't in that capped list at all yet.
    // The WO itself was just confirmed assigned, though, so look at ITS tasks directly instead of
    // leaving the operator with a toast confirming the assignment and no way to act on it.
    const detail = await load(() => api("frappe_wms.api.warehouse_order.warehouse_order_detail", { wo_name: wo }, { read: true }));
    task = detail && detail.tasks && detail.tasks.find((t) => t.status === "Open" || t.status === "Assigned");
  }
  if (task) nav.go(href("task", task.name)); else notify.ok(_("Assigned {0}", [wo]));
}

function taskCard(task) {
  const onHold = task.status === "On Hold";
  return Card({
    title: _(task.task_type), dim: onHold, attrs: { "data-task": task.name }, onClick: () => nav.go(href("task", task.name)),
    right: [StatusBadge(task.status), Badge(_(task.priority), task.priority)],
    meta: [task.product || "", h("br"), `${taskLocation(task, "src")} → ${taskLocation(task, "dst")}`,
      onHold ? [h("br"), h("span", { style: { color: "var(--warn-text)" } }, task.blocking_reason || _("Waiting on an earlier task in this Warehouse Order"))] : null],
    qty: `${fmtQty(task.confirmed_quantity || 0)} / ${fmtQty(task.planned_quantity)} ${task.stock_uom || ""}${task.wave ? " · " + task.wave : ""}`,
  });
}

export default {
  id: "tasks", pattern: "tasks/:group",
  title: (ctx) => ctx.params.group === "found" ? _("Pick tasks") : ({ inbound: _("Putaway tasks"), internal: _("Internal tasks"), outbound: _("Pick tasks") })[ctx.params.group] || _("Tasks"),
  crumb: (ctx) => sectionCrumb(ctx.params.group === "found" ? "outbound" : ctx.params.group),
  parent: (ctx) => ctx.params.group === "found" ? "#/picking" : sectionHash(ctx.params.group),
  async enter(ctx) {
    if (ctx.params.group === "found" && !st.found.length) return { redirect: "#/picking" };
    if (!SECTIONS[ctx.params.group] && ctx.params.group !== "found") return { redirect: "#/" };
    if (ctx.params.group !== "found") await refresh();
  },
  refresh: () => refresh(),
  render(ctx) {
    const wrap = h("div");
    const group = ctx.params.group;
    let tasks = group === "found" ? st.found : S.tasks.filter((t) => (TASK_TYPE_GROUPS[group] || []).includes(t.task_type));
    if (group === "found") wrap.append(Hint(st.foundLabel));
    if (st.loading && !st.loaded && !tasks.length) { wrap.append(Loading()); return wrap; }
    if (!tasks.length) {
      wrap.append(Empty(_("No open tasks right now.")));
      if (S.resource && !S.resource.current_queue) wrap.append(h("div.hint", { style: { textAlign: "center" } }, _("Join a queue under Device & session to receive work automatically.")));
      return wrap;
    }
    // Tasks of one Warehouse Order are shown as a numbered timeline in their sequence.
    const groups = []; const byWO = {};
    tasks.forEach((t) => {
      if (!t.warehouse_order) { groups.push({ wo: null, tasks: [t] }); return; }
      if (!byWO[t.warehouse_order]) { byWO[t.warehouse_order] = { wo: t.warehouse_order, tasks: [] }; groups.push(byWO[t.warehouse_order]); }
      byWO[t.warehouse_order].tasks.push(t);
    });
    groups.forEach((g) => {
      if (!g.wo) { wrap.append(taskCard(g.tasks[0])); return; }
      const sorted = [...g.tasks].sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
      const confirmed = sorted.filter((t) => t.status === "Confirmed").length;
      wrap.append(Section({ title: `${g.wo} · ${_("{0}/{1} confirmed", [confirmed, sorted.length])}` },
        sorted.map((t, i) => h("div.timeline-row", h("div.timeline-dot", { class: t.status === "Confirmed" ? "done" : t.status === "On Hold" ? "pending" : "active" }, i + 1), h("div.timeline-card", taskCard(t))))));
    });
    return wrap;
  },
  actions: () => (S.resource && S.resource.current_queue ? { primary: { label: _("Get next work"), icon: "⚡", run: pullWork } } : null),
};
