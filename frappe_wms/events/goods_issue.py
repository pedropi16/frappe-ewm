from frappe_wms.services.issue import post_goods_issue, reverse_goods_issue

def on_submit(doc, method=None): post_goods_issue(doc)
def on_cancel(doc, method=None): reverse_goods_issue(doc)
