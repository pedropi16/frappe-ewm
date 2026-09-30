"""Units an operator may count in. The WMS ledger always posts in the product's stock UOM; the
RF screens let people count in whatever the goods come in (a case of 12, a pallet of 480) and
convert before posting."""
import frappe
from frappe.utils import flt


def unit_options(item, stock_uom, preferred_uom=None, preferred_factor=None):
    """[{"uom", "factor"}] - the stock UOM first (factor 1), then the document line's own unit
    (an order in cases), then the ERPNext Item's UOM conversions."""
    units = [{"uom": stock_uom, "factor": 1}]
    candidates = []
    if preferred_uom and preferred_uom != stock_uom and flt(preferred_factor) > 0:
        candidates.append((preferred_uom, flt(preferred_factor)))
    if item:
        candidates += [(c.uom, flt(c.conversion_factor)) for c in frappe.get_all("UOM Conversion Detail",
                       filters={"parent": item, "parenttype": "Item"}, fields=["uom", "conversion_factor"], order_by="idx asc")]
    for uom, factor in candidates:
        if uom and uom != stock_uom and factor > 0 and not any(u["uom"] == uom for u in units):
            units.append({"uom": uom, "factor": factor})
    return units
