frappe.pages["wms-inbound-delivery"].on_page_load = function (wrapper) { wms_delivery_page.boot(wrapper, "Inbound Delivery"); };
frappe.pages["wms-inbound-delivery"].on_page_show = function (wrapper) { wms_delivery_page.shown(wrapper); };
