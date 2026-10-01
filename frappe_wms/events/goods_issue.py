from frappe_wms.services.issue import post_goods_issue, reverse_goods_issue
from frappe_wms.services.erp_sync_queue import dispatch

def on_submit(doc, method=None):
    post_goods_issue(doc)
    dispatch("goods_issue", doc)

def on_cancel(doc, method=None):
    reverse_goods_issue(doc)
    dispatch("goods_issue_reversal", doc)
