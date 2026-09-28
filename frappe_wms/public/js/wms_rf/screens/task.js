import { h } from "#wms/ui/dom.js";
import { S, nav, load, run, notify, update } from "#wms/app.js";
import { api } from "#wms/core/api.js";
import { _ } from "#wms/core/i18n.js";
import { Section, Field, KV, Btn, Expect, Stepper, Empty, Loading, StatusBadge, Badge, Hint } from "#wms/ui/kit.js";
import { fmtQty, flt, round6, parseNum, isNumeric, matchExpected } from "#wms/core/util.js";
import { feedback } from "#wms/core/feedback.js";
import { href } from "#wms/core/routes.js";
import { saveDraft, loadDraft, clearDraft, ensureKey, refreshSession, taskLocation, flushDrafts, sectionCrumb } from "#wms/screens/shared.js";
import { groupOfType } from "#wms/screens/tasks.js";

// Task confirmation: one history entry per step (#/task/WT-1/quantity), so hardware Back steps back, reload lands on the
// same step with everything scanned so far, and a completed task can never be re-entered by pressing Back.
const st = { name: null, task: null, loading: false, form: null, fetchedAt: 0, codes: null, exc: { remarks: "", revised: "" } };
const draftKey = (name) => `task:${name}`;

const STEP_LABEL = () => ({ source: _("Scan source"), product: _("Scan product"), quantity: _("Quantity"), destination: _("Scan destination"), review: _("Review & confirm") });

function buildSteps(task) {
  const steps = [];
  if (task.source_bin || task.source_hu) steps.push("source");
  if (S.settings.require_scan_verification && task.product) steps.push("product");
  steps.push("quantity");
  if (task.destination_bin || task.destination_hu) steps.push("destination");
  steps.push("review");
  return steps;
}
const remaining = (t) => round6(flt(t.planned_quantity) - flt(t.confirmed_quantity));

function completed(stepKey, f) {
  return { source: f.srcOk, product: f.prodOk, quantity: f.qtyOk, destination: f.dstOk, review: false }[stepKey];
}

function newForm(task) {
  return { src: "", srcOk: false, prod: "", prodOk: false, qty: fmtQty(remaining(task)), qtyOk: false, dst: "", dstOk: false, hu: "", idem: "", w0: nav.depth, base: flt(task.confirmed_quantity) };
}

function persist(immediate, step) {
  if (!st.form || !st.task) return;
  saveDraft(draftKey(st.name), st.form, { label: `${_(st.task.task_type)} · ${st.task.product || st.name}`, route: href("task", st.name, step || currentStep() || "") });
  if (immediate) flushDrafts();
}
const currentStep = () => (S.route && S.route.params && S.route.params.step) || null;

async function fetchTask(name, force) {
  if (!force && st.name === name && st.task && Date.now() - st.fetchedAt < 30000) return true;
  const fresh = await load(() => api("frappe_wms.api.scanner.get_task", { task_name: name }, { read: true }));
  if (!fresh) return false;
  st.task = fresh; st.fetchedAt = Date.now();
  return true;
}

export default {
  id: "task", pattern: "task/:name/:step?/:code?",
  title: () => (st.task ? _(st.task.task_type) : _("Task")),
  crumb: () => (st.task ? sectionCrumb(groupOfType(st.task.task_type)) : null),
  parent: () => (st.task ? `#/tasks/${groupOfType(st.task.task_type)}` : "#/"),

  async enter(ctx) {
    const name = ctx.params.name;
    if (st.name !== name) {
      st.name = name; st.form = null; st.codes = null; st.fetchedAt = 0; st.exc = { remarks: "", revised: "" };
      st.task = S.tasks.find((t) => t.name === name) || null; // paint the list row's data at once; refreshed below
    }
    st.loading = !st.task; update();
    const ok = await fetchTask(name, ctx.meta.initial);
    st.loading = false;
    if (!ok && !st.task) return { redirect: "#/tasks/inbound" };
    const t = st.task;
    if (t.status === "Confirmed" || t.docstatus === 1) { clearDraft(draftKey(name)); notify.info(_("This task is already confirmed.")); return { redirect: `#/tasks/${groupOfType(t.task_type)}` }; }
    if (["Cancelled", "Exception", "On Hold"].includes(t.status) || t.docstatus === 2) { update(); return; }

    if (!st.form) st.form = loadDraft(draftKey(name)) || newForm(t);
    // Entering from outside (list tap, resume from menu, reload) starts a new stretch of history entries for this wizard.
    const fromSameTask = S.prevRoute && S.prevRoute.id === "task" && S.prevRoute.params.name === name;
    if (!fromSameTask && (ctx.meta.push || ctx.meta.initial)) st.form.w0 = nav.depth;
    const steps = buildSteps(t);
    const step = ctx.params.step;
    if (step === "exception") {
      if (!st.codes) st.codes = await load(() => api("frappe_wms.api.scanner.list_exception_codes", { task_type: t.task_type }, { read: true })) || [];
      update(); return;
    }
    // Guard: never land on a step whose earlier steps are not done (deep link, stale history entry, reload).
    const firstOpen = steps.find((s) => s !== "review" && !completed(s, st.form)) || "review";
    const wanted = steps.includes(step) ? step : firstOpen;
    if (steps.indexOf(wanted) > steps.indexOf(firstOpen)) return { redirect: href("task", name, firstOpen) };
    if (wanted !== step) return { redirect: href("task", name, wanted) };
    update();
  },

  refresh: async () => { if (st.name) { await fetchTask(st.name, true); update(); } },

  render(ctx) {
    const t = st.task;
    if (!t) return st.loading ? Loading() : Empty(_("Task not found."));
    const wrap = h("div");
    wrap.append(summary(t));
    if (t.status === "On Hold") { wrap.append(h("div.notice.warn", { style: { borderRadius: "10px" } }, "\u{1F512} " + (t.blocking_reason || _("Waiting on an earlier task in this Warehouse Order.")))); return wrap; }
    if (["Cancelled", "Exception"].includes(t.status) || t.docstatus === 2) { wrap.append(Section({ title: _("Not available") }, h("div", _("This task is {0} and cannot be confirmed.", [_(t.status)])))); return wrap; }
    if (!st.form) return Loading();
    const step = ctx.params.step;
    if (step === "exception") return exceptionView(wrap, ctx);
    return stepView(wrap, step);
  },

  actions(ctx) {
    const t = st.task;
    if (!t || !st.form || ["On Hold", "Cancelled", "Exception"].includes(t.status)) return null;
    const step = ctx.params.step;
    if (step === "exception") {
      if (ctx.params.code) return { primary: { label: _("Report exception"), kind: "danger", run: reportException } };
      return null;
    }
    if (!step) return null;
    const exc = { label: _("Exception"), key: "F7", kind: "danger", run: () => nav.go(href("task", st.name, "exception")) };
    return { primary: step === "review" ? { label: _("Confirm"), icon: "✓", run: confirmTask } : { label: _("Next"), run: () => next(step) }, secondary: [exc] };
  },
};

function summary(t) {
  const rem = remaining(t);
  const box = Section({},
    h("div.row1", h("div.card-title", `${_(t.task_type)} · ${t.product || ""}`), h("div.card-right", StatusBadge(t.status), Badge(_(t.priority), t.priority))),
    h("div", { style: { height: "10px" } }),
    KV([[_("Planned"), `${fmtQty(t.planned_quantity)} ${t.stock_uom || ""}`], [_("Remaining"), `${fmtQty(rem)} ${t.stock_uom || ""}`], [_("From"), taskLocation(t, "src")], [_("To"), taskLocation(t, "dst")]]));
  const alloc = t.stock_allocations || [];
  if (alloc.length > 1) {
    box.append(h("div.hint", { style: { marginTop: "10px" } }, _("Cluster pick - sort into {0} orders", [alloc.length])),
      alloc.map((r) => h("div.line-sub", `${r.outbound_delivery_number || r.outbound_delivery || r.stock_allocation} · ${fmtQty(r.allocated_quantity)} ${t.stock_uom || ""}`)));
  }
  return box;
}

function fail(name, message) {
  S.fieldErrors[name] = message; feedback.error(); S.focusRequest = name; update();
}

const KIND_LABEL = () => ({ bin: _("bin"), hu: _("Handling Unit"), item: _("item") });

// Says what the wrong scan actually was, so the operator can tell "wrong bin" from "scanned a product" from "unreadable".
async function explainMismatch(value, expected, what) {
  const exp = expected.filter(Boolean).join(` ${_("or")} `);
  let matches = [];
  try {
    const r = await api("frappe_wms.api.scanner.resolve_scan", { code: value }, { read: true, timeoutMs: 6000 });
    matches = r.matches || [];
  } catch (e) { /* offline: fall back to the generic message */ }
  const m = matches[0];
  if (m) return _("That is {0} {1}, but this task needs {2} {3}.", [KIND_LABEL()[m.type] || m.type, m.name, what, exp]);
  return _("{0} is not a known code. Expected {1}.", [value, exp]);
}

function stepView(wrap, step) {
  const t = st.task, f = st.form;
  const steps = buildSteps(t);
  const idx = steps.indexOf(step);
  if (idx < 0) return Loading();
  wrap.append(Stepper(steps.length, idx, steps.map((s) => STEP_LABEL()[s])));
  const box = Section({});
  if (step === "source") {
    box.append(Expect(_("Scan source"), [t.source_bin, t.source_hu]),
      Field({ name: "src", kind: "scan", label: _("Source bin or Handling Unit"), placeholder: _("Scan barcode"), value: f.src, autofocus: true,
        onInput: (v) => { f.src = v; f.srcOk = false; persist(); },
        onCommit: async (v) => {
          const m = matchExpected(v, [t.source_bin, t.source_hu]);
          if (!m) return explainMismatch(v, [t.source_bin, t.source_hu], _("source"));
          f.src = m; f.srcOk = true; return advance("source");
        } }));
  } else if (step === "product") {
    box.append(Expect(_("Scan product"), [t.product]),
      Field({ name: "prod", kind: "scan", label: _("Product barcode or code"), placeholder: _("Scan the item"), value: f.prod, autofocus: true,
        onInput: (v) => { f.prod = v; f.prodOk = false; persist(); },
        onCommit: async (v) => {
          if (matchExpected(v, [t.product])) { f.prod = t.product; f.prodOk = true; return advance("product"); }
          let matches = [];
          try { matches = (await api("frappe_wms.api.scanner.resolve_scan", { code: v }, { read: true, timeoutMs: 6000 })).matches || []; } catch (e) { /* offline */ }
          const item = matches.find((m) => m.type === "item");
          if (item && item.name === t.product) { f.prod = t.product; f.prodOk = true; return advance("product"); }
          if (item) return _("That is item {0}, but this task needs {1}.", [item.name, t.product]);
          const other = matches[0];
          return other ? _("That is {0} {1}, not a product. Expected {2}.", [KIND_LABEL()[other.type], other.name, t.product]) : _("{0} is not a known product code. Expected {1}.", [v, t.product]);
        } }));
  } else if (step === "quantity") {
    const rem = remaining(t);
    box.append(Field({ name: "qty", kind: "qty", label: `${_("Quantity")} · ${_("remaining {0}", [fmtQty(rem)])}`, value: f.qty, unit: t.stock_uom, autofocus: true,
        hint: rem > 1 ? _("Confirming less than planned keeps the task open for the rest.") : null,
        onInput: (v) => { f.qty = v; f.qtyOk = false; persist(); } }),
      Btn({ label: _("All remaining ({0})", [fmtQty(rem)]), small: true, onClick: () => { f.qty = fmtQty(rem); persist(); S.focusRequest = "qty"; update(); } }));
  } else if (step === "destination") {
    box.append(Expect(_("Scan destination"), [t.destination_bin, t.destination_hu]),
      Field({ name: "dst", kind: "scan", label: _("Destination bin or Handling Unit"), placeholder: _("Scan barcode"), value: f.dst, autofocus: true,
        onInput: (v) => { f.dst = v; f.dstOk = false; persist(); },
        onCommit: async (v) => {
          const m = matchExpected(v, [t.destination_bin, t.destination_hu]);
          if (!m) return explainMismatch(v, [t.destination_bin, t.destination_hu], _("destination"));
          f.dst = m; f.dstOk = true; return advance("destination");
        } }));
  } else if (step === "review") {
    const excess = round6(parseNum(f.qty) - remaining(t));
    box.append(KV([[_("Product"), t.product], [_("Quantity"), `${f.qty} ${t.stock_uom || ""}`], [_("From"), f.src || taskLocation(t, "src")], [_("To"), f.dst || taskLocation(t, "dst")]]));
    if (excess > 0) box.append(Hint(_("{0} more than planned ({1}) - the extra goes to the warehouse's difference bin, not here.", [fmtQty(excess), fmtQty(remaining(t))])));
    box.append(h("div", { style: { height: "14px" } }),
      Field({ name: "hu", kind: "scan", label: _("Destination Handling Unit (optional)"), placeholder: _("Scan or leave as suggested"), value: f.hu,
        hint: _("Defaults to {0} if left as is.", [t.destination_hu || t.source_hu || _("no HU")]), onInput: (v) => { f.hu = v; persist(); }, submitOnEmpty: true,
        onCommit: () => { persist(); } }));
  }
  wrap.append(box);
  return wrap;
}

// Moves to the step after `stepKey`; a scan/Enter that completes a step calls this so the operator never taps Next.
function advance(stepKey) {
  const steps = buildSteps(st.task);
  const nextStep = steps[steps.indexOf(stepKey) + 1];
  persist(true, nextStep);
  if (nextStep) nav.go(href("task", st.name, nextStep));
}

function next(step) {
  const t = st.task, f = st.form;
  if (step === "quantity") {
    if (!isNumeric(f.qty)) return fail("qty", _("Enter a number."));
    const qty = parseNum(f.qty);
    if (qty <= 0) return fail("qty", _("Enter a quantity greater than zero."));
    // More than planned is allowed (a real find, e.g. Unload/Putaway) - it is capped at the task's
    // own plan server-side and the excess is posted to the warehouse's difference bin instead of
    // being silently absorbed or blocked outright; the review step below says so.
    f.qty = fmtQty(qty); f.qtyOk = true; return advance("quantity");
  }
  // A scan step advanced with the button instead of a scan: validate what is typed in the field.
  const field = S.fields.find((x) => x.name === { source: "src", product: "prod", destination: "dst" }[step]);
  if (field) { if (!field.input.value.trim()) return fail(field.name, _("Scan the code shown above.")); return field.spec.onCommit(field.input.value.trim()).then((err) => { if (err) fail(field.name, err); }); }
}

async function confirmTask() {
  const t = st.task, f = st.form;
  const qty = parseNum(f.qty);
  if (!(qty > 0)) { nav.go(href("task", st.name, "quantity")); return; }
  // Based on what was confirmed when this draft began, not now: after a lost response + reload the task already shows the
  // new total, and a key derived from that would no longer match the first attempt's - defeating the server-side dedupe.
  const key = `${t.name}:${fmtQty(f.base != null ? f.base : t.confirmed_quantity || 0)}:${ensureKey(f, "TC")}`;
  persist(true);
  const result = await run(() => api("frappe_wms.api.scanner.confirm_task", {
    task_name: t.name, scanned_source: f.src || undefined, scanned_destination: f.dst || undefined, scanned_product: f.prod || undefined,
    confirmed_quantity: qty, destination_hu: f.hu || undefined, idempotency_key: key,
  }), { label: _("Confirming…"), again: confirmTask });
  if (!result) return; // error shown with Retry; the draft (and its idempotency key) is kept, so a resend cannot double-post
  feedback.done();
  const w0 = f.w0; const type = t.task_type;
  clearDraft(draftKey(t.name));
  st.form = null; st.fetchedAt = 0;
  await run(refreshSession, { busy: false, exclusive: false });
  const released = result.released_tasks || [];
  const nextTask = released.length ? S.tasks.find((x) => released.includes(x.name)) : null;
  let msg = _("{0} {1} ({2})", [_(type), _(result.status || "Confirmed").toLowerCase(), fmtQty(result.quantity != null ? result.quantity : qty)]);
  const excess = round6(qty - flt(result.quantity != null ? result.quantity : qty));
  if (excess > 0) msg += ` — ${_("{0} extra sent to the difference bin", [fmtQty(excess)])}`;
  if (result.sort_task) msg += ` — ${_("a Sort task was created to move it on")}`;
  const steps = Math.max(0, nav.depth - (Number.isInteger(w0) ? w0 : nav.depth - 1));
  if (nextTask) { notify.ok(`${msg} — ${_("next: {0} · {1}", [_(nextTask.task_type), taskLocation(nextTask, "src")])}`); nav.unwind(steps, href("task", nextTask.name)); }
  else { notify.ok(msg); nav.unwind(steps + 1, `#/tasks/${groupOfType(type)}`); }
}

function exceptionView(wrap, ctx) {
  const t = st.task;
  const code = ctx.params.code;
  if (!code) {
    const box = Section({ title: _("Select exception"), hint: _("Reporting an exception blocks this task for a supervisor.") });
    if (!st.codes) box.append(Loading());
    else if (!st.codes.length) box.append(Hint(_("No exception codes are configured for this task type. Ask a supervisor to set one up under WMS Exception Code.")));
    (st.codes || []).forEach((c) => box.append(Btn({ label: c.exception_name + (c.requires_supervisor ? " \u{1F512}" : ""), kind: "danger", onClick: () => { st.exc = { remarks: "", revised: fmtQty(remaining(t)) }; nav.go(href("task", st.name, "exception", c.name)); } })));
    wrap.append(box);
    return wrap;
  }
  const c = (st.codes || []).find((x) => x.name === code);
  if (!c) { if (st.codes) return Empty(_("Unknown exception code.")); return Loading(); }
  const box = Section({ title: c.exception_name });
  if (c.allows_quantity_change) {
    box.append(Hint(_("Enter the quantity actually found. The task closes at this amount and the shortfall raises a replenishment request.")),
      Field({ name: "revised", kind: "qty", label: _("Quantity found"), value: st.exc.revised, unit: t.stock_uom, autofocus: true, onInput: (v) => { st.exc.revised = v; } }));
  }
  box.append(Field({ name: "remarks", kind: "text", label: c.requires_comment ? _("Comment (required)") : _("Comment (optional)"), placeholder: _("What happened?"), value: st.exc.remarks, autofocus: !c.allows_quantity_change, onInput: (v) => { st.exc.remarks = v; } }));
  wrap.append(box);
  return wrap;
}

async function reportException() {
  const t = st.task;
  const c = (st.codes || []).find((x) => x.name === currentCode());
  if (!c) return;
  const remarks = st.exc.remarks.trim();
  if (c.requires_comment && !remarks) return fail("remarks", _("This exception requires a comment."));
  let revised;
  if (c.allows_quantity_change) {
    if (!isNumeric(st.exc.revised)) return fail("revised", _("Enter a number."));
    revised = parseNum(st.exc.revised);
    if (revised < 0) return fail("revised", _("Quantity cannot be negative."));
    if (revised < flt(t.confirmed_quantity)) return fail("revised", _("Cannot be less than the {0} already confirmed.", [fmtQty(t.confirmed_quantity)]));
  }
  if (!confirm_(_("Block this task with “{0}”?", [c.exception_name]))) return;
  const ok = await run(() => api("frappe_wms.api.scanner.raise_exception", { task_name: t.name, exception_code: c.name, remarks: remarks || undefined, revised_quantity: revised }), { label: _("Reporting…"), again: reportException });
  if (!ok) return;
  feedback.warn();
  clearDraft(draftKey(t.name));
  const w0 = st.form && st.form.w0;
  st.form = null; st.fetchedAt = 0;
  await run(refreshSession, { busy: false, exclusive: false });
  notify.ok(_("{0} flagged as exception", [_(t.task_type)]));
  nav.unwind(Math.max(0, nav.depth - (Number.isInteger(w0) ? w0 : nav.depth - 1)) + 1, `#/tasks/${groupOfType(t.task_type)}`);
}
const currentCode = () => (S.route && S.route.params.code) || null;
const confirm_ = (msg) => window.confirm(msg);
