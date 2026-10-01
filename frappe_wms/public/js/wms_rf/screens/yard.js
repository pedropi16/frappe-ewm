import { h } from "#wms/ui/dom.js";
import { S, nav, run, load, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, Card, StatusBadge, Badge, KV, Hint, Loading, Empty } from "#wms/ui/kit.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { finishFlow, enteredFresh, sectionCrumb, matchScan } from "#wms/screens/shared.js";

// Gate and dock: the day's dock appointments (services/yard.py) - check trucks in, send them to a
// door, complete and check them out, and take in trucks that arrive without an appointment.
const M = (m) => `frappe_wms.api.yard.${m}`;
const st = { board: null, loading: false, name: null, spot: "", door: "", w0: 0, nw: { vehicle: "", direction: "Inbound", spot: "" } };
const warehouse = () => S.resource && S.resource.warehouse;
const time = (v) => (v ? String(v).slice(11, 16) : "");
const GROUPS = [["At Door", "At the door"], ["Checked In", "In the yard"], ["Planned", "Expected"], ["Completed", "Done, still on site"]];

async function fetchBoard() {
  if (!warehouse()) return;
  st.loading = true; update();
  st.board = await load(() => api(M("yard_board"), { warehouse: warehouse() }, { read: true }));
  st.loading = false; update();
}
const find = (name) => (st.board ? st.board.appointments.find((a) => a.name === name) : null);

export const yardList = {
  id: "yard", pattern: "yard",
  title: () => _("Yard"), crumb: () => sectionCrumb("inbound"), parent: () => "#/s/inbound",
  enter: fetchBoard,
  refresh: fetchBoard,
  render() {
    if (!warehouse()) return Empty(_("Log on to a resource first."));
    if (!st.board) return Loading();
    const b = st.board;
    const wrap = h("div", Section({ title: _("Doors") }, h("div.chips", b.doors.map((d) => {
      const a = find(d.occupied_by);
      return Badge(`${d.door}: ${a ? a.vehicle_registration || a.name : _("free")}`, a ? "High" : "Done");
    }))));
    for (const [status, label] of GROUPS) {
      const rows = b.appointments.filter((a) => a.status === status);
      if (!rows.length) continue;
      wrap.append(Section({ title: `${_(label)} (${rows.length})` }, rows.map((a) => Card({
        title: a.vehicle_registration || a.name, right: StatusBadge(a.status),
        meta: [`${_(a.direction)} · ${time(a.planned_start)}–${time(a.planned_end)}`, a.door ? ` · ${_("Door")} ${a.door}` : "", a.yard_bin ? ` · ${a.yard_bin}` : "", a.walk_in ? ` · ${_("no appointment")}` : ""],
        qty: a.carrier || a.inbound_delivery || a.shipment || "", onClick: () => nav.go(href("yard", a.name)) }))));
    }
    if (!b.appointments.length) wrap.append(Empty(_("No trucks today.")));
    return wrap;
  },
  actions: () => ({ primary: { label: _("Truck without appointment"), icon: "+", run: () => nav.go("#/yard-new") } }),
};

async function act(method, args, done) {
  const r = await run(() => api(M(method), { appointment: st.name, ...args }), { label: _("Saving…") });
  if (r === undefined) return false;
  feedback.ok(); notify.ok(done(r), { ttl: 2500 });
  await fetchBoard();
  return r;
}

export const yardDetail = {
  id: "yard-detail", pattern: "yard/:name",
  title: () => _("Truck"), crumb: () => sectionCrumb("inbound"), parent: () => "#/yard",
  async enter(ctx) {
    if (st.name !== ctx.params.name) Object.assign(st, { name: ctx.params.name, spot: "", door: "" });
    if (enteredFresh(ctx, "yard-detail")) st.w0 = nav.depth;
    if (!find(st.name)) await fetchBoard();
    if (!find(st.name)) return { redirect: "#/yard" };
  },
  refresh: fetchBoard,
  render() {
    const a = find(st.name);
    if (!a) return Loading();
    const wrap = h("div", Section({ title: `${a.vehicle_registration || a.name} · ${_(a.status)}` }, KV([
      [_("Direction"), _(a.direction)], [_("Slot"), `${time(a.planned_start)}–${time(a.planned_end)}`], [_("Door"), a.door || "-"],
      [_("Carrier"), a.carrier], [_("Trailer"), a.trailer_number], [_("Carries"), a.inbound_delivery || a.shipment], [_("Yard spot"), a.yard_bin]])));
    if (a.status === "Planned") wrap.append(Section({ hint: _("At the gate: scan the yard spot the truck parks on, or leave it empty.") },
      Field({ name: "spot", kind: "scan", label: _("Yard spot (optional)"), placeholder: _("Scan yard bin"), value: st.spot, autofocus: true, submitOnEmpty: true,
        onInput: (v) => { st.spot = v; }, onCommit: (v) => { st.spot = v; return checkIn(); } })));
    if (a.status === "Planned" || a.status === "Checked In") wrap.append(Section({ hint: _("Scan the door the truck backs onto, or leave it empty for its booked door.") },
      Field({ name: "door", kind: "scan", label: _("Door"), placeholder: a.door || _("Scan door"), value: st.door, autofocus: a.status === "Checked In", submitOnEmpty: true,
        onInput: (v) => { st.door = v; }, onCommit: async (v) => {
          const doors = (st.board.doors || []).map((d) => d.door);
          const door = v ? await matchScan(v, doors) : null;
          if (v && !door) return _("{0} is not a door of this warehouse.", [v]);
          return toDoor(door);
        } })));
    return wrap;
  },
  actions() {
    const a = find(st.name);
    if (!a) return null;
    const out = { Planned: { primary: { label: _("Check in"), run: checkIn }, secondary: [{ label: _("Cancel appointment"), kind: "danger", run: cancelIt }] },
      "Checked In": { primary: { label: _("To door"), run: () => toDoor(null) }, secondary: [{ label: _("Check out"), run: checkOut }] },
      "At Door": { primary: { label: _("Unloaded / loaded"), run: complete }, secondary: [{ label: _("Check out"), run: checkOut }] },
      Completed: { primary: { label: _("Check out"), run: checkOut } } };
    return out[a.status] || null;
  },
};

async function checkIn() {
  const r = await run(() => api(M("check_in"), { warehouse: warehouse(), appointment: st.name, yard_bin: st.spot.trim() || undefined }), { label: _("Checking in…") });
  if (r === undefined) return false;
  feedback.ok(); notify.ok(_("{0} checked in", [st.name]), { ttl: 2500 }); st.spot = "";
  await fetchBoard();
}
const toDoor = async (door) => { const r = await act("to_door", { door: door || undefined }, (x) => _("Go to door {0}", [x.door])); if (r) st.door = ""; return r === false ? false : undefined; };
const complete = () => act("complete", {}, () => _("{0} done", [st.name]));
async function checkOut() { if (await act("check_out", {}, () => _("{0} checked out", [st.name]))) finishFlow(st.w0, "#/yard"); }
async function cancelIt() {
  if (!confirm(_("Cancel appointment {0}?", [st.name]))) return;
  if (await act("cancel", {}, () => _("{0} cancelled", [st.name]))) finishFlow(st.w0, "#/yard");
}

export const yardNew = {
  id: "yard-new", pattern: "yard-new",
  title: () => _("Truck without appointment"), crumb: () => sectionCrumb("inbound"), parent: () => "#/yard",
  enter(ctx) { if (enteredFresh(ctx, "yard-new")) { st.nw = { vehicle: "", direction: "Inbound", spot: "" }; st.w0 = nav.depth; } },
  render() {
    const n = st.nw;
    return h("div", Section({ hint: _("A truck at the gate with no booking. The warehouse decides whether it may come in.") },
      Field({ name: "vehicle", kind: "scan", label: _("Vehicle registration"), placeholder: _("Type or scan"), value: n.vehicle, autofocus: true, onInput: (v) => { n.vehicle = v; }, onCommit: (v) => { n.vehicle = v; } }),
      Field({ name: "direction", kind: "select", label: _("The truck"), value: n.direction, onInput: (v) => { n.direction = v; },
        options: [{ value: "Inbound", label: _("delivers goods (inbound)") }, { value: "Outbound", label: _("collects goods (outbound)") }] }),
      Field({ name: "spot", kind: "scan", label: _("Yard spot (optional)"), placeholder: _("Scan yard bin"), value: n.spot, onInput: (v) => { n.spot = v; }, onCommit: (v) => { n.spot = v; } }),
      Hint(_("Book trucks ahead in the WMS Monitor's Yard & Doors view."))));
  },
  actions: () => ({ primary: { label: _("Check in"), run: walkIn } }),
};

async function walkIn(confirmed = 0) {
  const n = st.nw;
  if (!n.vehicle.trim()) { S.fieldErrors.vehicle = _("Enter the vehicle registration."); feedback.error(); S.focusRequest = "vehicle"; update(); return; }
  const r = await run(() => api(M("check_in"), { warehouse: warehouse(), vehicle_registration: n.vehicle.trim(), direction: n.direction,
    yard_bin: n.spot.trim() || undefined, confirm_without_appointment: confirmed }), { label: _("Checking in…") });
  if (r === undefined) return;
  if (r.needs_confirmation) { feedback.warn(); if (confirm(r.needs_confirmation)) return walkIn(1); return; }
  feedback.done();
  await fetchBoard();
  nav.replace(href("yard", r.appointment));
}
