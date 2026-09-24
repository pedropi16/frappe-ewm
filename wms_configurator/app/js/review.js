import * as Schema from "./schema.js";
import * as Store from "./store.js";
import { el } from "./render.js";
import { exportProfile } from "./export_import.js";
import { applyProfileToSite } from "./push.js";
import { getConnection } from "./connect.js";

export function renderReviewStep(container) {
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
  const conn = getConnection();
  pushCard.appendChild(el("p", { class: "card-desc" }, conn.url ? `Connected to ${conn.url}. Requires that site's site_config.json to allow CORS from this page's origin.` : "Not connected yet - use the Connect button in the top bar."));
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
