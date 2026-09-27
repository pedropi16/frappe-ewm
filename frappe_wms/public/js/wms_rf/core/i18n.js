// Translation lookup for the scanner page. A custom www page has none of the desk's __()/frappe.boot plumbing, so the
// page injects {English: translated} for the session language (see www/wms/index.py) and _() looks strings up in it.
// args follows Frappe's own convention: "{0}"/"{1}" placeholders filled positionally.
let messages = {};

export function setMessages(m) { messages = m || {}; }

export function _(text, args) {
  let out = (messages && messages[text]) || text;
  if (args) args.forEach((a, i) => { out = out.split(`{${i}}`).join(String(a)); });
  return out;
}
