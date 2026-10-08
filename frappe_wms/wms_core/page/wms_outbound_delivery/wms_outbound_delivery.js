frappe.pages["wms-outbound-delivery"].on_page_load = function (wrapper) { wms_delivery_page.boot(wrapper, "Outbound Delivery"); };
frappe.pages["wms-outbound-delivery"].on_page_show = function (wrapper) { wms_delivery_page.shown(wrapper); };
