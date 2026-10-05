frappe.query_reports["WMS Resource Activity"] = {
  filters: [
    { fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "WMS Warehouse", reqd: 1 },
    { fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -7) },
    { fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.get_today() },
  ],
};
