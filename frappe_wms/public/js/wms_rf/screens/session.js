import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Btn, KV, Field, Hint, Loading } from "#wms/ui/kit.js";
import { prefs, setPref } from "#wms/core/prefs.js";
import { refreshSession, clearDraft, draftMeta } from "#wms/screens/shared.js";
import { drafts } from "#wms/app.js";

// Device & session: resource, queue, work center, and per-device preferences.
const st = { queues: null, centers: null };

async function act(method, args, msg, label) {
  const ok = await run(async () => { await api(method, args); await refreshSession(); return true; }, { label });
  if (ok) notify.ok(msg);
  return ok;
}

export default {
  id: "session", pattern: "session",
  title: () => _("Device & session"),
  parent: () => "#/",
  async enter() {
    st.queues = null; st.centers = null;
    const r = S.resource || {};
    if (!r.current_queue) {
      const rows = await load(() => api("frappe_wms.api.warehouse_order.list_queues", { warehouse: r.warehouse }, { read: true }));
      st.queues = rows || [];
      update();
    }
  },
  render() {
    const r = S.resource || {};
    const wrap = h("div");
    wrap.append(Section({ title: _("Device") },
      KV([[_("User"), window.WMS.fullname], [_("Resource"), r.name], [_("Resource group"), r.resource_group || _("None")], [_("Warehouse"), r.warehouse]]),
      h("div", { style: { height: "12px" } }),
      Btn({ label: _("Log off this device"), kind: "danger", onClick: async () => {
        if (!confirm(_("Log off this device?"))) return;
        const ok = await act("frappe_wms.api.resource.log_off", {}, _("Logged off"), _("Logging off…"));
        if (ok !== undefined) { S.resource = null; nav.replace("#/logon"); }
      } })));

    // Queue: a resource-group setting, not something an operator picks for themselves (EWM-
    // style - a resource group is pre-wired to the queues it serves; joining/leaving one is a
    // supervisor action in the desk, never a scanner-app action). This is read-only information:
    // either the one queue a supervisor pinned this resource to, or - more usually - every queue
    // its resource group covers, any of which "Get next work" can pull from.
    const queueSection = Section({ title: _("Queue"), hint: r.current_queue ? _("Pinned by a supervisor to this one queue.") : _("From your resource group - ask a supervisor to change it.") });
    if (r.current_queue) {
      queueSection.append(h("div", "\u{1F4E1} " + r.current_queue));
    } else if (st.queues) {
      if (!st.queues.length) queueSection.append(Hint(_("No queues assigned to your resource group yet.")));
      st.queues.forEach((q) => queueSection.append(h("div", `\u{1F4E1} ${q.queue_name} (${_(q.activity)})`)));
    } else {
      queueSection.append(Loading());
    }
    wrap.append(queueSection);

    // Work center (optional)
    const wcSection = Section({ title: _("Work center (optional)"), hint: _("Only needed for VAS, Packing and Kitting - it pre-fills the work-center bin.") });
    if (r.current_work_center) {
      wcSection.append(h("div", "\u{1F3ED} " + r.current_work_center), h("div", { style: { height: "10px" } }),
        Btn({ label: _("Leave work center"), onClick: () => act("frappe_wms.api.resource.log_off_work_center", {}, _("Logged off Work Center"), _("Leaving…")) }));
    } else if (st.centers) {
      if (!st.centers.length) wcSection.append(Hint(_("No Work Centers configured for your warehouse.")));
      st.centers.forEach((wc) => wcSection.append(Btn({ label: `${wc.work_center_name || wc.work_center_code} (${wc.bin})`, onClick: async () => { if (await act("frappe_wms.api.resource.log_on_work_center", { work_center_code: wc.name }, _("Logged on to {0}", [wc.work_center_code]), _("Logging on…"))) { st.centers = null; update(); } } })));
    } else {
      wcSection.append(Btn({ label: _("Log on to a work center"), onClick: async () => {
        const rows = await load(() => api("frappe_wms.api.resource.list_available_work_centers", { warehouse: r.warehouse }, { read: true }));
        if (rows) { st.centers = rows; update(); }
      } }));
    }
    wrap.append(wcSection);

    // Preferences
    const p = prefs();
    const toggle = (key, label) => Btn({ label: `${label}: ${p[key] ? _("On") : _("Off")}`, onClick: () => { setPref(key, !p[key]); update(); } });
    wrap.append(Section({ title: _("This device") },
      toggle("sound", _("Sound")),
      toggle("vibrate", _("Vibration")),
      toggle("keyboard", _("On-screen keyboard for scan fields")),
      Field({ name: "theme", kind: "select", label: _("Theme"), value: p.theme, options: [{ value: "auto", label: _("Match device") }, { value: "dark", label: _("Dark") }, { value: "light", label: _("Light") }], onInput: (v) => setPref("theme", v) }),
      Hint(_("The on-screen keyboard stays hidden on scan fields so a hardware scanner or the camera is used; tap the keyboard key next to a field to type once."))));

    const pendingDrafts = draftMeta();
    wrap.append(Section({ title: _("Unfinished work") },
      pendingDrafts.length ? pendingDrafts.map((d) => h("div.meta", d.label)) : Hint(_("Nothing unfinished on this device.")),
      pendingDrafts.length ? Btn({ label: _("Discard all unfinished work"), kind: "danger", onClick: () => { if (confirm(_("Discard all unfinished work on this device?"))) { drafts.clearAll(); update(); } } }) : null));
    wrap.append(Btn({ label: _("Reload app"), onClick: () => location.reload() }));
    return wrap;
  },
};
