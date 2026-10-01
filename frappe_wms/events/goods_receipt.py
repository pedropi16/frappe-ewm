from frappe_wms.services.receipt import post_goods_receipt, reverse_goods_receipt
from frappe_wms.services.erp_sync_queue import dispatch

def on_submit(doc, method=None):
    post_goods_receipt(doc)
    dispatch("goods_receipt", doc)

def on_cancel(doc, method=None):
    reverse_goods_receipt(doc)
    dispatch("goods_receipt_reversal", doc)
