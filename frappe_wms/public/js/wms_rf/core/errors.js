import { _ } from "#wms/core/i18n.js";

// kind: network | timeout | session | permission | validation | server
// retryable means "the same request may safely be sent again" - true wherever the outcome was never confirmed.
export class AppError extends Error {
  constructor(message, { kind = "server", status = 0, retryable = false, raw = null } = {}) {
    super(message);
    this.name = "AppError";
    this.kind = kind;
    this.status = status;
    this.retryable = retryable;
    this.raw = raw;
  }
}

const ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " " };

// Frappe server messages are HTML fragments ("<b>Item</b> not found"). The scanner shows plain text.
export function htmlToText(html) {
  return String(html == null ? "" : html)
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<\/(p|div|li)>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (m) => ENTITIES[m])
    .replace(/\s+/g, " ")
    .trim();
}

// Pulls the human message out of a Frappe error response body. _server_messages is a JSON string of JSON strings.
// Tracebacks and exception class names ("frappe.exceptions.ValidationError: ...") never reach the operator.
export function messageFromBody(data) {
  if (!data || typeof data !== "object") return "";
  const out = [];
  if (data._server_messages) {
    try {
      for (const item of JSON.parse(data._server_messages)) {
        let text = item;
        try { const parsed = JSON.parse(item); text = parsed.message != null ? parsed.message : item; } catch (e) { /* plain string */ }
        const clean = htmlToText(text);
        if (clean && !out.includes(clean)) out.push(clean);
      }
    } catch (e) { /* malformed - fall through */ }
  }
  if (out.length) return out.join(" ");
  if (typeof data.exception === "string" && data.exception) {
    const m = data.exception.split("\n").filter(Boolean).pop() || "";
    const tail = m.includes(": ") ? m.slice(m.indexOf(": ") + 2) : "";
    if (tail && !/^\s*(Traceback|File )/.test(tail)) return htmlToText(tail);
  }
  return "";
}

export function friendlyStatus(status) {
  if (status === 403) return _("You are not allowed to do this. Ask a supervisor if you need access.");
  if (status === 404) return _("That could not be found.");
  if (status === 409 || status === 417) return _("That could not be completed.");
  if (status >= 500) return _("The server had a problem. Try again in a moment.");
  return _("Request failed ({0}).", [status]);
}

// Turns anything thrown into text safe to show an operator.
export function errorText(e) {
  if (!e) return _("Something went wrong.");
  if (e instanceof AppError) return e.message;
  return (e.message && String(e.message)) || _("Something went wrong.");
}
