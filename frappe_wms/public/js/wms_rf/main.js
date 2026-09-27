import { S, registerScreen, startNavigation, lastRoute, notify, update } from "#wms/app.js";
import { api, configureApi } from "#wms/core/api.js";
import { setMessages, _ } from "#wms/core/i18n.js";
import { applyTheme } from "#wms/core/prefs.js";
import { mountShell } from "#wms/ui/shell.js";
import { installKeys } from "#wms/ui/keys.js";
import { installDraftFlush, refreshSession } from "#wms/screens/shared.js";

import logon from "#wms/screens/logon.js";
import { menu, section } from "#wms/screens/menu.js";
import session from "#wms/screens/session.js";
import lookup from "#wms/screens/lookup.js";
import tasks from "#wms/screens/tasks.js";
import task from "#wms/screens/task.js";
import move from "#wms/screens/move.js";
import { receiveList, receiveDetail } from "#wms/screens/receive.js";
import { shipList, shipDetail } from "#wms/screens/ship.js";
import pack from "#wms/screens/pack.js";
import { countList, countDetail } from "#wms/screens/count.js";
import { qualityList, qualityDetail } from "#wms/screens/quality.js";
import { loadList, loadDetail } from "#wms/screens/load.js";
import { deconScan, deconDetail } from "#wms/screens/decon.js";
import closeMovement from "#wms/screens/closemove.js";
import { repackScan, repackDetail } from "#wms/screens/repack.js";
import { huList, huNew, huDetail } from "#wms/screens/hu.js";
import kitting from "#wms/screens/kitting.js";
import { consolidationList, consolidationDetail } from "#wms/screens/consolidation.js";
import { pickingMenu, pickingManual, pickingFind } from "#wms/screens/picking.js";
import { vasList, vasNew, vasDetail } from "#wms/screens/vas.js";

// Literal routes come before ":param" routes so "hu-new" / "vas-new" can never be read as a name.
const SCREENS = [
  logon, menu, section, session, lookup, tasks, task, move,
  receiveList, receiveDetail, shipList, shipDetail, pack, countList, countDetail, qualityList, qualityDetail, loadList, loadDetail,
  deconScan, deconDetail, closeMovement, repackScan, repackDetail, huList, huNew, huDetail, kitting, consolidationList, consolidationDetail,
  pickingMenu, pickingManual, pickingFind, vasList, vasNew, vasDetail,
];

async function boot() {
  const W = window.WMS;
  setMessages(W.messages);
  configureApi({
    csrf: W.csrf,
    onSessionExpired: () => { location.href = "/login?redirect-to=/wms"; },
  });
  applyTheme();
  mountShell(document.getElementById("app"));
  installKeys();
  installDraftFlush();
  SCREENS.forEach(registerScreen);

  try { await refreshSession(); } catch (e) { notify.error(e.message || _("Could not reach the server.")); }
  S.booted = true;
  window.WMS_BOOTED = true;

  // Reload / return after login lands on the same screen: the hash wins, then the last screen this tab showed.
  const hash = location.hash && location.hash !== "#" && location.hash !== "#/" ? location.hash : lastRoute() || "#/";
  startNavigation(window, hash);

  // A phone that slept or lost signal: bring the visible screen up to date when the operator comes back.
  let hiddenAt = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { hiddenAt = Date.now(); return; }
    if (hiddenAt && Date.now() - hiddenAt > 30000 && S.screen && S.screen.refresh && !S.busy) S.screen.refresh(S.ctx());
    hiddenAt = 0;
  });
  window.addEventListener("pageshow", (e) => { if (e.persisted) { update(); if (S.screen && S.screen.refresh) S.screen.refresh(S.ctx()); } });
}

boot().catch((e) => {
  console.error("[wms-rf] boot failed", e);
  const box = document.getElementById("boot") || document.getElementById("app");
  if (box) box.textContent = `${_("The scanner app failed to start.")} ${e && e.message ? e.message : ""}`;
});
