// Stand-alone Repack Center (SAP /SCWM/PACK) for packers (who don't get the full WMS Monitor): the same
// component as the Monitor's "Packing Station" view - see public/js/wms_packing_station.js.
frappe.pages["wms-packing-station"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Repack Center"), single_column: true });
  frappe.require("/assets/frappe_wms/js/wms_packing_station.js", async () => {
    const warehouses = await frappe.db.get_list("WMS Warehouse", { fields: ["name"], limit: 200 });
    let warehouse = warehouses.length === 1 ? warehouses[0].name : null;
    const $top = $(`<div style="margin-bottom:10px;"></div>`).appendTo(page.main);
    const $body = $(`<div></div>`).appendTo(page.main);
    const station = new WMSPackingStation($body, () => warehouse);
    if (warehouses.length > 1) {
      let saved = null;
      try { saved = localStorage.getItem("wms_packing_station_wh"); } catch (e) { /* storage blocked */ }
      warehouse = warehouses.some((w) => w.name === saved) ? saved : null;
      $top.html(`<select class="form-control input-sm" style="width:220px;"><option value="">${__("Select a warehouse")}</option>${warehouses.map((w) =>
        `<option value="${frappe.utils.escape_html(w.name)}" ${w.name === warehouse ? "selected" : ""}>${frappe.utils.escape_html(w.name)}</option>`).join("")}</select>`);
      $top.find("select").on("change", (e) => {
        warehouse = e.target.value || null;
        try { localStorage.setItem("wms_packing_station_wh", warehouse || ""); } catch (err) { /* storage blocked */ }
        station.render();
      });
    }
    station.render();
  });
};
