// Tiny hyperscript: h("div.card#x", {onclick, "data-k": 1}, "text", child, [more]) -> HTMLElement.
// Text goes in as text nodes, never as HTML, so a bin code or product name containing "<" cannot inject markup.
const NS_SVG = "http://www.w3.org/2000/svg";

export function h(tag, attrs, ...children) {
  let name = tag, id = null, classes = [];
  const m = String(tag).match(/^([a-zA-Z0-9]*)((?:[.#][\w-]+)*)$/);
  if (m) {
    name = m[1] || "div";
    for (const part of m[2].match(/[.#][\w-]+/g) || []) {
      if (part[0] === "#") id = part.slice(1); else classes.push(part.slice(1));
    }
  }
  const el = document.createElement(name);
  if (id) el.id = id;
  if (classes.length) el.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs.nodeType || Array.isArray(attrs))) { children.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = (el.className ? el.className + " " : "") + v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value" || k === "checked" || k === "disabled" || k === "selected") el[k] = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false || c === true) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c.nodeType) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export function svg(markup) {
  const t = document.createElementNS(NS_SVG, "svg");
  t.innerHTML = markup;
  return t;
}
