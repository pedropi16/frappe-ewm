frappe.query_reports["WMS Bin Utilization"] = {
  filters: [
    { fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "WMS Warehouse", reqd: 1 },
    { fieldname: "storage_type", label: __("Storage Type"), fieldtype: "Link", options: "Storage Type" },
  ],
};
