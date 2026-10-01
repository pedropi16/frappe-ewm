"""GS1-128 / GS1 DataMatrix element strings - the server twin of public/js/wms_rf/core/gs1.js.

A pallet or case label carries several Application Identifiers in one barcode: (00) SSCC,
(01) GTIN, (10) batch, (17) expiry, (21) serial, (30)/(37) count. Scanners send FNC1 as the GS
character between variable-length fields; people type the human-readable "(01)...(10)..." form.
parse() returns None for anything that is not a GS1 element string.
"""
import re

GS = "\x1d"
AIS = {"00": ("sscc", 18, None), "01": ("gtin", 14, None), "02": ("content_gtin", 14, None), "10": ("batch", None, 20),
       "11": ("production_date", 6, None), "15": ("best_before", 6, None), "17": ("expiry", 6, None), "21": ("serial", None, 20),
       "30": ("count", None, 8), "37": ("count", None, 8)}
DATE_FIELDS = ("production_date", "best_before", "expiry")


def _date(v):
    if not re.fullmatch(r"\d{6}", v or ""): return v
    yy, mm, dd = int(v[:2]), int(v[2:4]), int(v[4:])
    year = (1900 if yy >= 50 else 2000) + yy
    if dd == 0:
        import calendar
        dd = calendar.monthrange(year, mm)[1]
    return f"{year:04d}-{mm:02d}-{dd:02d}"


def _finish(out):
    for k in DATE_FIELDS:
        if out.get(k): out[k] = _date(out[k])
    if out.get("count") is not None:
        try: out["count"] = float(out["count"])
        except ValueError: out.pop("count")
    return out


def parse(raw):
    s = (raw or "").strip()
    if not s: return None
    if re.match(r"^\](C1|e0|d2|Q3)", s): s = s[3:]
    if s.startswith("("):
        out = {}
        for ai, value in re.findall(r"\((\d{2,4})\)([^(]*)", s):
            if ai not in AIS: return None
            out[AIS[ai][0]] = value.strip()
        return _finish(out) if out else None
    if not re.match(r"^(00|01|02|10|11|15|17|21|30|37)", s) or not re.fullmatch(r"[\x20-\x7e\x1d]+", s): return None
    out, i = {}, 0
    while i < len(s):
        if s[i] == GS:
            i += 1
            continue
        ai = s[i:i + 2]
        if ai not in AIS: return None
        name, fixed, maxlen = AIS[ai]
        i += 2
        if fixed:
            value = s[i:i + fixed]
            if len(value) != fixed or not value.isdigit(): return None
            i += fixed
        else:
            end = s.find(GS, i)
            stop = len(s) if end == -1 else end
            value = s[i:min(stop, i + maxlen)]
            i += len(value)
        out[name] = value
    if len(out) < 2 and not (out.get("gtin") and len(s) == 16) and not out.get("sscc"): return None
    return _finish(out)


def gtin_variants(code):
    """A printed GTIN-14 against the EAN-13 / UPC-12 usually stored as the item barcode."""
    code = str(code or "")
    out, t = [code], code
    while len(t) > 8 and t.startswith("0"):
        t = t[1:]
        out.append(t)
    if code.isdigit() and len(code) in (8, 12, 13):
        out.append(code.zfill(14))
    return list(dict.fromkeys(out))
