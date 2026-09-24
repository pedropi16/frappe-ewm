import * as Store from "./store.js";

function slug(name) {
  return (name || "wms-profile").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "wms-profile";
}

export function exportProfile() {
  const data = Store.serializeForExport();
  const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug(data.profileName)}.wms-profile.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function importProfileFromFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        if (!data || typeof data !== "object" || !data.records) {
          reject(new Error("This doesn't look like a WMS configuration profile (missing 'records')."));
          return;
        }
        Store.loadProfile(data);
        resolve(data);
      } catch (e) {
        reject(new Error(`Could not parse file: ${e.message}`));
      }
    };
    reader.onerror = () => reject(new Error("Could not read file."));
    reader.readAsText(file);
  });
}
