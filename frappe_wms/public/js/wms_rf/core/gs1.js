// GS1-128 / GS1 DataMatrix label parsing for RF scans.
//
// A supplier pallet or case label carries several Application Identifiers in one barcode:
// (00) SSCC, (01) GTIN, (10) batch, (17) expiry, (21) serial, (30)/(37) count ... A scanner sends
// them either with FNC1 as the ASCII GS character between variable-length fields, or (typed or
// printed human-readable) with the AIs in parentheses. Returns null for anything that is not a
// GS1 element string, so an ordinary bin/HU/product code passes through untouched.

const GS = "\u001d";
// AI -> [name, fixed length or null for variable (max)]
const AIS = {
  "00": ["sscc", 18], "01": ["gtin", 14], "02": ["content_gtin", 14], "10": ["batch", null, 20], "11": ["production_date", 6],
  "15": ["best_before", 6], "17": ["expiry", 6], "21": ["serial", null, 20], "30": ["count", null, 8], "37": ["count", null, 8],
};

function yymmdd(v) {
  if (!/^\d{6}$/.test(v)) return v;
  const yy = Number(v.slice(0, 2)), mm = v.slice(2, 4); let dd = v.slice(4, 6);
  const year = (yy >= 50 ? 1900 : 2000) + yy;
  if (dd === "00") dd = String(new Date(year, Number(mm), 0).getDate()).padStart(2, "0"); // "00" = last day of month
  return `${year}-${mm}-${dd}`;
}

function finish(out) {
  for (const k of ["production_date", "best_before", "expiry"]) if (out[k]) out[k] = yymmdd(out[k]);
  if (out.count != null) out.count = Number(out.count);
  return out;
}

export function parseGS1(raw) {
  let s = String(raw == null ? "" : raw).trim();
  if (!s) return null;
  if (/^\](C1|e0|d2|Q3)/.test(s)) s = s.slice(3); // GS1 symbology identifiers
  // Human-readable form: (01)09501101530003(17)250101(10)ABC
  if (s.startsWith("(")) {
    const out = {};
    const re = /\((\d{2,4})\)([^(]*)/g;
    let m, any = false;
    while ((m = re.exec(s))) {
      const def = AIS[m[1]];
      if (!def) return null;
      out[def[0]] = m[2].trim(); any = true;
    }
    return any ? finish(out) : null;
  }
  // Raw element string: must start with a known AI and contain only what GS1 allows.
  if (!/^(00|01|02|10|11|15|17|21|30|37)/.test(s) || !/^[\x20-\x7e\u001d]+$/.test(s)) return null;
  const out = {};
  let i = 0;
  while (i < s.length) {
    if (s[i] === GS) { i++; continue; }
    const ai = s.slice(i, i + 2);
    const def = AIS[ai];
    if (!def) return null;
    i += 2;
    const [name, fixed, max] = def;
    let value;
    if (fixed) {
      value = s.slice(i, i + fixed);
      if (value.length !== fixed || !/^\d+$/.test(value)) return null;
      i += fixed;
    } else {
      const end = s.indexOf(GS, i);
      const stop = end === -1 ? s.length : end;
      value = s.slice(i, Math.min(stop, i + max));
      i += value.length;
    }
    out[name] = value;
  }
  // A plain 13/14-digit EAN that happens to start with "01" is not a GS1 string - require
  // the full 16 characters of (01) or at least two elements.
  if (Object.keys(out).length < 2 && !(out.gtin && s.length === 16) && !out.sscc) return null;
  return finish(out);
}

// GTIN-14 as printed on labels vs the EAN-13/UPC-12 usually stored as the item barcode.
export function gtinVariants(gtin) {
  const g = String(gtin || "");
  const out = new Set([g]);
  let t = g;
  while (t.length > 8 && t[0] === "0") { t = t.slice(1); out.add(t); }
  return [...out];
}
