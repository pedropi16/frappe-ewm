import * as Schema from "./schema.js";
import * as Store from "./store.js";
import { el } from "./render.js";
import { exportProfile } from "./export_import.js";
import { applyProfileToSite } from "./push.js";
import * as ERP from "./erp.js";
import { findBroken, fixAll } from "./refs.js";
import { compareWithSite, pullAll } from "./sitesync.js";
import { toast } from "./render.js";

export function renderReviewStep(container) {
  container.appendChild(referencesCard());
  container.appendChild(syncCard());

  const summary = el("div", { class: "card" });
  summary.appendChild(el("h2", {}, "Summary"));
  const table = el("table", { class: "record-table" });
  const rows = Schema.schema().in_scope
    .map((name) => [name, Store.countRecords(name)])
    .filter(([, count]) => count > 0);
  if (!rows.length) {
    table.appendChild(el("tbody", {}, el("tr", { class: "empty-row" }, el("td", {}, "Nothing configured yet."))));
  } else {
    table.appendChild(el("tbody", {}, rows.map(([name, count]) => el("tr", {}, [el("td", {}, name), el("td", {}, String(count))]))));
  }
  summary.appendChild(table);
  container.appendChild(summary);

  const exportCard = el("div", { class: "card" });
  exportCard.appendChild(el("h2", {}, "Export profile"));
  exportCard.appendChild(el("p", { class: "card-desc" }, "Downloads a JSON file you can hand to another system, keep for version control, or apply later via the bench import script."));
  exportCard.appendChild(el("button", { class: "btn btn-primary", type: "button", onclick: exportProfile }, "Download profile JSON"));
  container.appendChild(exportCard);

  const benchCard = el("div", { class: "card" });
  benchCard.appendChild(el("h2", {}, "Apply on the server (bench)"));
  benchCard.appendChild(el("p", { class: "card-desc" }, "Safest path for a production site - no CORS or API key needed. Copy the exported file to the bench host, then run:"));
  benchCard.appendChild(el("pre", { class: "apply-log" }, "bench --site <site> execute frappe_wms.setup.import_profile.import_profile \\\n  --kwargs \"{'path': '/path/to/your-profile.wms-profile.json'}\""));
  container.appendChild(benchCard);

  const pushCard = el("div", { class: "card" });
  pushCard.appendChild(el("h2", {}, "Apply directly to a connected site"));
  const st = ERP.status();
  pushCard.appendChild(el("p", { class: "card-desc" }, st.mode === "session"
    ? `Connected to ${st.label} as ${st.user}. Records that already exist are updated in place; new ones are created. Nothing is ever deleted.`
    : st.mode === "token" ? `Connected to ${st.label} with an API key. The site must allow CORS from this page's origin. Existing records are updated in place; nothing is deleted.`
    : "Not connected yet - use the Connect button in the top bar (or open this page from the site while signed in)."));
  const log = el("div", { class: "apply-log" }, "");
  const pushBtn = el("button", {
    class: "btn btn-primary", type: "button",
    onclick: async () => {
      pushBtn.disabled = true;
      log.textContent = "Applying...\n";
      try {
        const results = await applyProfileToSite((entry) => {
          const line = `${entry.ok ? "OK  " : "FAIL"}  ${entry.doctypeName}: ${entry.label} - ${entry.message}\n`;
          const span = document.createElement("span");
          span.className = entry.ok ? "ok" : "err";
          span.textContent = line;
          log.appendChild(span);
        });
        const failed = results.filter((r) => !r.ok).length;
        log.appendChild(document.createTextNode(`\nDone: ${results.length} records processed, ${failed} failed.`));
      } catch (e) {
        log.appendChild(document.createTextNode(`\nError: ${e.message}`));
      } finally {
        pushBtn.disabled = false;
      }
    },
  }, "Apply to connected site");
  pushCard.appendChild(pushBtn);
  pushCard.appendChild(log);
  container.appendChild(pushCard);
}

function referencesCard() {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "Check references"));
  const body = el("div", {}, el("p", { class: "card-desc" }, "Checking…"));
  card.appendChild(body);
  (async () => {
    let broken = findBroken();
    const online = ERP.isConnected();
    // a value with no match in the profile is fine if the site already has that record (e.g. seeded stock types)
    if (online) {
      const onSite = await Promise.all(broken.map((b) => (b.suggestion ? false : ERP.exists(b.target, b.value))));
      broken = broken.filter((b, i) => !onSite[i]);
    }
    const fixable = broken.filter((b) => b.suggestion);
    body.innerHTML = "";
    if (!broken.length) {
      body.appendChild(el("p", { class: "card-desc" }, online ? "Every reference resolves - to a record in this profile or one that already exists on the site." : "Every reference to another record in this profile resolves. (Not connected, so references to records that only exist on a site can't be checked.)"));
      return;
    }
    body.appendChild(el("p", { class: "card-desc" }, `${broken.length} reference(s) point at a record that isn't in this profile${online ? " or on the site" : ""}. Look for typos or short codes (e.g. BULK instead of DC1-BULK).${online ? "" : " Connect to a site to rule out records that already exist there."}`));
    if (fixable.length) body.appendChild(el("button", { type: "button", class: "btn btn-primary btn-small", onclick: () => { const n = fixAll(); toast(`Corrected ${n} reference(s)`); } }, `Fix ${fixable.length} automatically`));
    body.appendChild(el("ul", { class: "map-list" }, broken.slice(0, 40).map((b) => el("li", {}, [
      el("strong", {}, `${b.doctype} ${b.label}`), ` · ${b.field}: `, el("code", {}, b.value),
      b.suggestion ? " → " : null,
      b.suggestion ? el("code", {}, b.suggestion) : el("span", { class: "hint" }, " (no match)"),
    ]))));
    if (broken.length > 40) body.appendChild(el("p", { class: "hint" }, `…and ${broken.length - 40} more`));
  })();
  return card;
}

function syncCard() {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "Sync with the site"));
  const st = ERP.status();
  if (st.mode === "none") {
    card.appendChild(el("p", { class: "card-desc" }, "Connect to a site (top bar) to pull its existing configuration into this profile, edit it in bulk, and see exactly what applying would change."));
    return card;
  }
  card.appendChild(el("p", { class: "card-desc" }, `Pull what already exists on ${st.label} into this profile to edit it in bulk, then compare before applying. Pulled records are marked ● and are updated in place when applied.`));
  const out = el("div", { class: "sync-out" });
  const pullBtn = el("button", { type: "button", class: "btn btn-small", onclick: async () => {
    if (!confirm(`Pull every configuration doctype from ${st.label}? Records that match ones already in this profile are overwritten with the site's values.`)) return;
    pullBtn.disabled = true; out.textContent = "Pulling…";
    const results = await pullAll((n) => { out.textContent = `Pulling ${n}…`; });
    pullBtn.disabled = false;
    const got = results.filter((r) => !r.error);
    out.textContent = `Pulled ${got.reduce((a, r) => a + r.added + r.updated, 0)} record(s) from ${got.length} doctype(s).` + (results.some((r) => r.error) ? ` ${results.filter((r) => r.error).length} could not be read: ${results.filter((r) => r.error).map((r) => r.doctypeName).join(", ")}.` : "");
  } }, "↓ Pull everything from the site");
  const cmpBtn = el("button", { type: "button", class: "btn btn-small btn-primary", onclick: async () => {
    cmpBtn.disabled = true; out.textContent = "Comparing…";
    const report = await compareWithSite((n) => { out.textContent = `Comparing ${n}…`; });
    cmpBtn.disabled = false; out.innerHTML = "";
    out.appendChild(renderReport(report));
  } }, "Compare with the site");
  card.appendChild(el("div", { class: "card-actions" }, [pullBtn, cmpBtn]));
  card.appendChild(out);
  return card;
}

function renderReport(report) {
  const totals = report.reduce((t, r) => ({ add: t.add + (r.added?.length || 0), chg: t.chg + (r.changed?.length || 0) }), { add: 0, chg: 0 });
  const wrap = el("div", {});
  wrap.appendChild(el("p", {}, el("strong", {}, `Applying now would create ${totals.add} and update ${totals.chg} record(s).`)));
  const rows = report.filter((r) => r.error || r.added.length || r.changed.length);
  if (!rows.length) wrap.appendChild(el("p", { class: "hint" }, "The site already matches this profile."));
  for (const r of rows) {
    if (r.error) { wrap.appendChild(el("p", { class: "warn" }, `${r.doctypeName}: ${r.error}`)); continue; }
    const det = el("details", { class: "diff-group" }, [
      el("summary", {}, `${r.doctypeName} - ${r.added.length} new, ${r.changed.length} changed, ${r.same} unchanged${r.onlyOnSite ? `, ${r.onlyOnSite} only on the site` : ""}`),
    ]);
    if (r.added.length) det.appendChild(el("p", { class: "hint" }, `New: ${r.added.slice(0, 15).join(", ")}${r.added.length > 15 ? "…" : ""}`));
    for (const c of r.changed.slice(0, 25)) {
      det.appendChild(el("div", { class: "diff-rec" }, [el("strong", {}, c.label), el("ul", {}, c.changes.map((x) => el("li", {}, [`${x.field}: `, el("span", { class: "diff-old" }, x.old), " → ", el("span", { class: "diff-new" }, x.now)])))]));
    }
    if (r.changed.length > 25) det.appendChild(el("p", { class: "hint" }, `…and ${r.changed.length - 25} more changed`));
    wrap.appendChild(det);
  }
  return wrap;
}
