import { h } from "#wms/ui/dom.js";
import { S, nav, run, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Empty, Btn, KV, Hint } from "#wms/ui/kit.js";
import { fmtQty } from "#wms/core/util.js";

// Scan anything: the code is classified (bin / handling unit / item) and the matching stock overview is shown - no
// "what type is this?" dropdown for the operator to get wrong.
const st = { code: "", result: null, kind: null, candidates: [] };

async function lookup(code, kind) {
  st.code = code; st.result = null; st.kind = null; st.candidates = [];
  update();
  const resolved = await run(() => api("frappe_wms.api.scanner.resolve_scan", { code }, { read: true }), { busy: false, exclusive: false });
  if (!resolved) return;
  const stockish = resolved.matches.filter((m) => m.type === "bin" || m.type === "hu");
  const pick = kind ? stockish.find((m) => m.type === kind) : stockish.length === 1 ? stockish[0] : null;
  if (!pick) {
    if (stockish.length > 1) { st.candidates = stockish; update(); return; }
    const item = resolved.matches.find((m) => m.type === "item");
    return item ? _("That is item {0}{1}. Scan a bin or Handling Unit to see stock.", [item.name, item.item_name ? ` (${item.item_name})` : ""]) : _("Nothing found for {0}.", [code]);
  }
  const result = await run(() => api(pick.type === "hu" ? "frappe_wms.api.scanner.hu_overview" : "frappe_wms.api.scanner.bin_overview", pick.type === "hu" ? { hu_number: pick.name } : { bin_code: pick.name }, { read: true }), { busy: false, exclusive: false });
  if (result) { st.result = result; st.kind = pick.type; update(); }
}

export default {
  id: "lookup", pattern: "lookup",
  title: () => _("Lookup"),
  parent: () => "#/",
  enter() { st.result = null; st.candidates = []; st.code = ""; },
  render() {
    const wrap = h("div");
    wrap.append(Section({ hint: _("Scan a bin or Handling Unit barcode to see what is in it.") },
      Field({ name: "code", kind: "scan", label: _("Barcode"), placeholder: _("Scan or type a code"), value: st.code, autofocus: true, onInput: (v) => { st.code = v; }, onCommit: (v) => lookup(v) })));
    if (st.candidates.length) {
      wrap.append(Section({ title: _("Which one?"), hint: _("This code matches more than one thing.") },
        st.candidates.map((c) => Btn({ label: `${c.type === "hu" ? _("Handling Unit") : _("Storage Bin")} ${c.name}`, onClick: () => lookup(st.code, c.type) }))));
    }
    if (st.result) {
      const r = st.result;
      const title = st.kind === "hu" ? `${_("Handling Unit")} ${r.handling_unit.name}` : `${_("Storage Bin")} ${r.storage_bin.name}`;
      const info = st.kind === "hu"
        ? KV([[_("Bin"), r.handling_unit.current_bin], [_("Status"), _(r.handling_unit.status)], [_("Type"), r.handling_unit.hu_type], [_("Parent HU"), r.handling_unit.parent_hu]])
        : KV([[_("Warehouse"), r.storage_bin.warehouse], [_("Type"), r.storage_bin.storage_type]]);
      wrap.append(Section({ title }, info));
      const stock = r.stock || [];
      wrap.append(Section({ title: _("Stock") }, !stock.length ? Empty(_("No stock found."), "\u{1F4E6}") :
        h("table.result", h("thead", h("tr", [_("Product"), _("Qty"), st.kind === "hu" ? _("Bin") : _("HU"), _("Type")].map((x) => h("th", x)))),
          h("tbody", stock.map((row) => h("tr", h("td", row.product), h("td", `${fmtQty(row.quantity)} ${row.stock_uom || ""}`), h("td", (st.kind === "hu" ? row.storage_bin : row.handling_unit) || "-"), h("td", _(row.stock_type || ""))))))));
    }
    return wrap;
  },
  actions: () => ({ primary: { label: _("Look up"), run: () => st.code && lookup(st.code) } }),
};
