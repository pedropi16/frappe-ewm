import { run, notify } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Card, StatusBadge } from "#wms/ui/kit.js";
import { fmtQty } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { listScreen } from "#wms/screens/lists.js";

const kitting = listScreen({
  id: "kitting", pattern: "kitting", title: _("Kitting"), section: "internal", method: "frappe_wms.api.kitting.list_open_kitting_orders", empty: _("No open Kitting Orders."),
  card: (o) => Card({ title: o.kit_item, right: StatusBadge(o.status), meta: `${_(o.direction)} · ${o.work_center_bin || ""}`, qty: fmtQty(o.quantity), onClick: async () => {
    if (!confirm(_("Complete kitting order for {0}?", [o.kit_item]))) return;
    const ok = await run(() => api("frappe_wms.api.kitting.complete_kitting_order", { kitting_order_name: o.name }), { label: _("Completing…") });
    if (ok === undefined) return;
    feedback.done(); notify.ok(_("{0} completed", [o.name])); await kitting.refresh({});
  } }),
});
export default kitting;
