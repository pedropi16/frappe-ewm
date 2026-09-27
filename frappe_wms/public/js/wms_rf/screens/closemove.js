import { h } from "#wms/ui/dom.js";
import { S, run, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field } from "#wms/ui/kit.js";
import { feedback } from "#wms/core/feedback.js";
import { uid } from "#wms/core/util.js";
import { finishFlow, enteredFresh, sectionCrumb } from "#wms/screens/shared.js";
import { nav } from "#wms/app.js";

const st = { code: "", w0: 0 };
export async function closeMovement(hu, w0) {
  const result = await run(() => api("frappe_wms.api.movement.close_movement", { hu_name: hu, idempotency_key: `CM:${hu}:${uid()}` }), { label: _("Closing movement…"), again: () => closeMovement(hu, w0) });
  if (!result) return false;
  feedback.done();
  finishFlow(w0, "#/tasks/internal", _("{0} task(s) created toward {1}", [result.tasks.length, result.destination_bin]));
  return true;
}
export default {
  id: "close-movement", pattern: "close-movement",
  title: () => _("Close Movement"), crumb: () => sectionCrumb("internal"), parent: () => "#/s/internal",
  enter(ctx) { if (enteredFresh(ctx, "close-movement")) st.w0 = nav.depth; st.code = ""; },
  render() {
    return h("div", Section({ title: _("Close Movement"), hint: _("Scan the Handling Unit that just finished its current step. The system finds its next bin and opens a task there - confirm it by scanning the HU and the destination bin once it arrives. Run this again at each stop.") },
      Field({ name: "hu", kind: "scan", label: _("Handling Unit"), placeholder: _("Scan HU barcode"), value: st.code, autofocus: true, onInput: (v) => { st.code = v; },
        onCommit: async (v) => { const ok = await closeMovement(v, st.w0); return ok ? undefined : false; } })));
  },
  actions: () => ({ primary: { label: _("Close movement"), run: () => st.code.trim() && closeMovement(st.code.trim(), st.w0) } }),
};
