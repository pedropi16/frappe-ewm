frappe.pages["wms-yard"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("Shipping & Receiving"),
    single_column: true,
  });
  // The assets are served with a long max-age, so a browser kept running an old wms_selection.js next to
  // this (always fresh) page code after a deploy. A version in the URL that changes every 10 minutes
  // bounds that - cheap, and nothing to remember to bump. (frappe.require cannot take a query string.)
  const v = Math.floor(Date.now() / 600000);
  const load = (src) => new Promise((resolve, reject) => {
    const url = `${src}?v=${v}`;
    if (document.querySelector(`script[data-wms-src="${url}"]`)) { resolve(); return; }
    const el = document.createElement("script");
    el.src = url; el.dataset.wmsSrc = url; el.onload = () => resolve(); el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
  load("/assets/frappe_wms/js/wms_grid.js")
    .then(() => load("/assets/frappe_wms/js/wms_selection.js"))
    .then(() => load("/assets/frappe_wms/js/wms_packing_station.js"))
    .then(() => load("/assets/frappe_wms/js/wms_monitor_core.js"))
    .then(() => new WMSMonitor(page, { views: ["cockpit", "yard"] }))
    .catch((e) => frappe.msgprint({ title: __("Shipping & Receiving"), indicator: "red", message: frappe.utils.escape_html(e.message) }));
};

