import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Btn, KV, Field, Hint } from "#wms/ui/kit.js";
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
  enter() { st.queues = null; st.centers = null; },
  render() {
    const r = S.resource || {};
    const wrap = h("div");
    wrap.append(Section({ title: _("Device") },
      KV([[_("User"), window.WMS.fullname], [_("Resource"), r.name], [_("Warehouse"), r.warehouse], [_("Queue"), r.current_queue || _("Not joined")]]),
      h("div", { style: { height: "12px" } }),
      Btn({ label: _("Log off this device"), kind: "danger", onClick: async () => {
        if (!confirm(_("Log off this device?"))) return;
        const ok = await act("frappe_wms.api.resource.log_off", {}, _("Logged off"), _("Logging off…"));
        if (ok !== undefined) { S.resource = null; nav.replace("#/logon"); }
      } })));

    // Queue
    const queueSection = Section({ title: _("Queue") });
    if (r.current_queue) {
      queueSection.append(h("div", "\u{1F4E1} " + r.current_queue), h("div", { style: { height: "10px" } }),
        Btn({ label: _("Leave queue"), onClick: () => act("frappe_wms.api.warehouse_order.leave_queue", {}, _("Left queue"), _("Leaving…")) }));
    } else if (st.queues) {
      if (!st.queues.length) queueSection.append(Hint(_("No queues configured for your warehouse.")));
      st.queues.forEach((q) => queueSection.append(Btn({ label: `${q.queue_name} (${_(q.activity)})`, onClick: async () => { if (await act("frappe_wms.api.warehouse_order.join_queue", { queue_name: q.name }, _("Joined {0}", [q.queue_name]), _("Joining…"))) { st.queues = null; update(); } } })));
    } else {
      queueSection.append(Hint(_("Not joined to a queue")), Btn({ label: _("Join a queue"), kind: "primary", onClick: async () => {
        const rows = await load(() => api("frappe_wms.api.warehouse_order.list_queues", { warehouse: r.warehouse }, { read: true }));
        if (rows) { st.queues = rows; update(); }
      } }));
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
