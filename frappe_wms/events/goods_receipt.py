from frappe_wms.services.receipt import post_goods_receipt, reverse_goods_receipt
from frappe_wms.services import erpnext_sync

def on_submit(doc, method=None):
    post_goods_receipt(doc)
    erpnext_sync.sync_goods_receipt(doc)

def on_cancel(doc, method=None):
    reverse_goods_receipt(doc)
    erpnext_sync.reverse_goods_receipt(doc)
