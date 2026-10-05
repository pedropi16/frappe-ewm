frappe.ui.form.on("Activity Area", {
  refresh(frm) {
    if (frm.is_new()) return;
    frm.add_custom_button(__("Generate Walk Path"), () => {
      frappe.prompt({ fieldname: "activity", label: __("Activity"), fieldtype: "Select", options: "Pick\nPutaway\nInternal Move\nInventory Count\nReplenish", reqd: 1, default: "Pick" }, (v) => {
        frappe.call({ method: "frappe_wms.wms_core.doctype.activity_area.activity_area.generate_walk_path", args: { area: frm.doc.name, activity: v.activity }, freeze: true }).then((r) => {
          frappe.show_alert({ message: __("{0} bins sorted", [r.message]), indicator: "green" }); frm.reload_doc();
        });
      }, __("Walk path from the bins' coordinates"));
    });
  },
});
