import { h } from "#wms/ui/dom.js";
import { nav, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Badge, Loading, KV, Hint } from "#wms/ui/kit.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { finishFlow, enteredFresh, sectionCrumb, matchScan } from "#wms/screens/shared.js";
import { listScreen, findRow } from "#wms/screens/lists.js";
import { matchExpected } from "#wms/core/util.js";

export const loadList = listScreen({
  id: "load", pattern: "load", title: _("Load"), section: "outbound", method: "frappe_wms.api.shipping.list_loadable_shipments", args: () => ({}), empty: _("Nothing ready to load."),
  card: (s) => Card({ title: s.shipment_number, right: StatusBadge(s.status), meta: [s.route || "-", s.door ? ` · ${_("Door")} ${s.door}` : ""], qty: _("{0} / {1} loaded", [s.loaded_count, s.total_count]), onClick: () => nav.go(href("load", s.name)) }),
});

const st = { s: null, w0: 0, scan: "" };

async function loaded(hu) {
  const r = await run(() => api("frappe_wms.api.shipping.confirm_hu_loaded", { shipment_name: st.s.name, hu_name: hu }), { label: _("Confirming…"), again: () => loaded(hu) });
  if (!r) return _("Could not confirm {0}.", [hu]);
  feedback.ok();
  const row = st.s.handling_units.find((x) => x.handling_unit === hu);
  if (row && !row.loaded) { row.loaded = 1; st.s.loaded_count += 1; }
  st.s.status = r.shipment_status; st.scan = "";
  notify.ok(_("{0} loaded", [hu]), { ttl: 2500 });
  update();
}

export const loadDetail = {
  id: "load-detail", pattern: "load/:name",
  title: () => _("Load"), crumb: () => sectionCrumb("outbound"), parent: () => "#/load",
  async enter(ctx) {
    if (enteredFresh(ctx, "load-detail")) st.w0 = nav.depth;
    const s = await findRow(loadList, ctx.params.name, ctx);
    if (!s) { notify.warn(_("That shipment is no longer loadable.")); return { redirect: "#/load" }; }
    st.s = s; update();
  },
  render() {
    const s = st.s;
    if (!s) return Loading();
    const pending = s.handling_units.filter((x) => !x.loaded);
    return h("div",
      Section({ title: `${s.shipment_number} · ${_(s.status)}` }, KV([[_("Route"), s.route], [_("Door"), s.door || s.staging_bin], [_("Loaded"), `${s.loaded_count} / ${s.total_count}`]])),
      pending.length ? Section({ hint: _("Scan each Handling Unit as it goes on the truck, or tap it below.") },
        Field({ name: "hu", kind: "scan", label: _("Handling Unit"), placeholder: _("Scan HU barcode"), value: st.scan, autofocus: true, onInput: (v) => { st.scan = v; },
          onCommit: async (v) => {
            const m = await matchScan(v, pending.map((x) => x.handling_unit));
            if (!m) return s.handling_units.some((x) => matchExpected(v, [x.handling_unit])) ? _("{0} is already loaded.", [v]) : _("{0} is not on this shipment.", [v]);
            return loaded(m);
          } })) : null,
      Section({ title: _("Handling Units") }, s.handling_units.map((x) => Card({ title: x.handling_unit, right: Badge(x.loaded ? _("Loaded") : _("Pending"), x.loaded ? "Loaded" : "High"), onClick: x.loaded ? null : () => loaded(x.handling_unit) }))));
  },
  actions() {
    const s = st.s;
    return s && s.status === "Loaded" ? { primary: { label: _("Depart"), icon: "\u{1F69B}", run: depart } } : null;
  },
};

async function depart() {
  const s = st.s;
  if (!confirm(_("Depart shipment {0}?", [s.shipment_number]))) return;
  const ok = await run(() => api("frappe_wms.api.shipping.depart_shipment", { shipment_name: s.name }), { label: _("Departing…"), again: depart });
  if (ok === undefined) return;
  feedback.done();
  const w0 = st.w0; st.s = null;
  finishFlow(w0, "#/load", _("{0} departed", [s.shipment_number]));
}
