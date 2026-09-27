import { h } from "#wms/ui/dom.js";
import { S, nav, load, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Btn, Loading } from "#wms/ui/kit.js";
import { refreshSession } from "#wms/screens/shared.js";

// EWM-style logon: pick the device/resource you are working on this shift. Everything else requires one.
const st = { loading: false, resources: [], loaded: false };

async function fetchResources() {
  st.loading = true; update();
  const rows = await load(() => api("frappe_wms.api.resource.list_available_resources", {}, { read: true }));
  st.loading = false; st.loaded = true;
  if (rows) st.resources = rows;
  update();
}

async function pick(r) {
  const ok = await run(async () => { await api("frappe_wms.api.resource.log_on", { resource_code: r.name }); await refreshSession(); return true; }, { label: _("Logging on…") });
  if (!ok) return;
  notify.ok(_("Logged on to {0}", [r.resource_code || r.name]));
  const target = S.afterLogon && !S.afterLogon.startsWith("#/logon") ? S.afterLogon : "#/";
  S.afterLogon = null;
  nav.replace(target);
}

export default {
  id: "logon", pattern: "logon", root: true, needsResource: false,
  title: () => _("Log On"),
  async enter() {
    if (S.resource) { const t = S.afterLogon || "#/"; S.afterLogon = null; return { redirect: t }; }
    st.loaded = false; st.resources = [];
    await fetchResources();
  },
  refresh: fetchResources,
  render() {
    const wrap = h("div");
    wrap.append(Section({ title: _("Pick your device"), hint: _("Choose the device you are using this shift.") },
      st.loading && !st.loaded ? Loading(_("Connecting to warehouse…")) : null,
      st.loaded && !st.resources.length ? h("div.notice.warn", { style: { margin: "0 0 10px", borderRadius: "10px" } }, "\u{1F512} " + _("No free WMS Resource is available. Ask your supervisor to configure one, or to log out whoever is still on the one you need.")) : null,
      st.resources.map((r) => Btn({ icon: "\u{1F4E1}", label: `${r.resource_code} (${_(r.resource_type)}${r.warehouse ? " · " + r.warehouse : ""})`, onClick: () => pick(r) }))));
    return wrap;
  },
  actions: () => ({ primary: { label: _("Refresh"), kind: "secondary", run: fetchResources } }),
};
