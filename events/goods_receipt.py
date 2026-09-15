from frappe_wms.services.receipt import post_goods_receipt, reverse_goods_receipt

def on_submit(doc, method=None): post_goods_receipt(doc)
def on_cancel(doc, method=None): reverse_goods_receipt(doc)
