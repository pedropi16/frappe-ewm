from frappe_wms.services.issue import post_goods_issue, reverse_goods_issue
from frappe_wms.services import erpnext_sync

def on_submit(doc, method=None):
    post_goods_issue(doc)
    erpnext_sync.sync_goods_issue(doc)

def on_cancel(doc, method=None):
    reverse_goods_issue(doc)
    erpnext_sync.reverse_goods_issue(doc)
