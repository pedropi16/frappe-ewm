// Pure route matching for the hash-based router: "#/task/WT-0001/quantity?x=1" <-> {path, params, query}.
// Every screen state that must survive reload or Back lives in the hash, so reload lands where the operator was.

export function parseHash(hash) {
  let h = String(hash || "").replace(/^#/, "");
  let query = {};
  const q = h.indexOf("?");
  if (q >= 0) {
    for (const part of h.slice(q + 1).split("&")) {
      if (!part) continue;
      const [k, v = ""] = part.split("=");
      query[decodeURIComponent(k)] = decodeURIComponent(v);
    }
    h = h.slice(0, q);
  }
  const path = h.replace(/^\/+/, "").replace(/\/+$/, "");
  return { path, segments: path ? path.split("/").map((s) => decodeURIComponent(s)) : [], query };
}

export function compilePattern(pattern) {
  return pattern.split("/").filter(Boolean).map((seg) => {
    if (seg.startsWith(":")) return { name: seg.slice(1).replace(/\?$/, ""), optional: seg.endsWith("?") };
    return { literal: seg };
  });
}

export function matchPattern(compiled, segments) {
  const params = {};
  let i = 0;
  for (const part of compiled) {
    if (part.literal !== undefined) {
      if (segments[i] !== part.literal) return null;
      i++;
    } else if (segments[i] !== undefined && segments[i] !== "") {
      params[part.name] = segments[i];
      i++;
    } else if (!part.optional) {
      return null;
    }
  }
  return i === segments.length ? params : null;
}

// routes: [{id, pattern}] tried in order (put literal routes before ":param" ones).
export function resolveRoute(routes, hash) {
  const { segments, query } = parseHash(hash);
  for (const r of routes) {
    const params = matchPattern(r.compiled || (r.compiled = compilePattern(r.pattern)), segments);
    if (params) return { id: r.id, params, query, hash: buildHash(segments, query) };
  }
  return null;
}

export function buildHash(segments, query) {
  const path = "/" + (segments || []).map((s) => encodeURIComponent(s)).join("/");
  const qs = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  return "#" + path + (qs ? "?" + qs : "");
}

// href("task", name, "quantity") -> "#/task/WT-1/quantity"; empty/undefined parts are dropped.
export function href(...parts) {
  return buildHash(parts.filter((p) => p !== undefined && p !== null && p !== "").map(String), null);
}
