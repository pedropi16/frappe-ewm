import { h } from "#wms/ui/dom.js";
import { run, notify } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Card, StatusBadge, Btn } from "#wms/ui/kit.js";
import { feedback } from "#wms/core/feedback.js";
import { listScreen } from "#wms/screens/lists.js";

const pack = listScreen({
  id: "pack", pattern: "pack", title: _("Pack"), section: "outbound", method: "frappe_wms.api.scanner.list_open_packing_orders", empty: _("No open packing orders."),
  card: (o) => h("div", Card({ title: o.name, right: StatusBadge(o.status), meta: `${(o.source_hus || []).join(", ")} → ${(o.destination_hus || []).join(", ")}` }),
    Btn({ label: _("Complete packing"), kind: "primary", onClick: async () => {
      if (!confirm(_("Complete packing order {0}?", [o.name]))) return;
      const ok = await run(() => api("frappe_wms.api.scanner.complete_packing_order", { packing_order_name: o.name }), { label: _("Completing…") });
      if (ok === undefined) return;
      feedback.done(); notify.ok(_("Packing order {0} completed", [o.name])); await pack.refresh({});
    } })),
});
export default pack;
