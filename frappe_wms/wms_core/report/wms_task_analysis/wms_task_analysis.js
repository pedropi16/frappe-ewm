frappe.query_reports["WMS Task Analysis"] = {
  filters: [
    { fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "WMS Warehouse", reqd: 1 },
    { fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
    { fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.get_today() },
    { fieldname: "task_type", label: __("Task Type"), fieldtype: "Select", options: "\nUnload\nPutaway\nPick\nInternal Move\nDeconsolidation\nConsolidation\nPack\nStage\nLoad\nPosting Change\nInventory Count\nSort\nCross Dock" },
    { fieldname: "group_by", label: __("Group By"), fieldtype: "Select", options: "Task Type\nResource\nDay\nProduct", default: "Task Type" },
  ],
};
