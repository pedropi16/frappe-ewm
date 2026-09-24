import { loadSchema } from "./schema.js";
import * as Store from "./store.js";
import { initWizard, goToStep } from "./wizard.js";
import { initConnectModal } from "./connect.js";
import { exportProfile, importProfileFromFile } from "./export_import.js";

async function main() {
  await loadSchema();
  Store.loadFromLocalStorage();

  const nameInput = document.getElementById("profile-name");
  nameInput.value = Store.getProfile().profileName || "";
  nameInput.addEventListener("change", (e) => Store.setProfileName(e.target.value));
  Store.subscribe((profile) => {
    if (document.activeElement !== nameInput) nameInput.value = profile.profileName || "";
  });

  document.getElementById("btn-export").addEventListener("click", exportProfile);

  document.getElementById("file-import").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      await importProfileFromFile(file);
      goToStep("settings");
    } catch (err) {
      alert(err.message);
    } finally {
      e.target.value = "";
    }
  });

  document.getElementById("btn-reset").addEventListener("click", () => {
    if (confirm("Discard the current profile and start over? This cannot be undone (unless you've exported it).")) {
      Store.resetProfile();
      goToStep(0);
    }
  });

  initConnectModal();
  initWizard();

  const hasData = Object.values(Store.getProfile().records).some((r) => r.length);
  if (hasData) goToStep("settings");
}

main().catch((err) => {
  document.getElementById("app").innerHTML = `<div style="padding:24px">Failed to start: ${err.message}</div>`;
  console.error(err);
});
