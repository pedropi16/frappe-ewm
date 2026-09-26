const SESSION_KEY = "wms-configurator-connection";

export function getConnection() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : { url: "", key: "", secret: "" };
  } catch {
    return { url: "", key: "", secret: "" };
  }
}

export function setConnection(conn) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(conn));
  } catch (e) {
    console.warn("could not store connection in sessionStorage", e);
  }
}

export function clearConnection() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch {}
}

function authHeader(conn) {
  return { Authorization: `token ${conn.key}:${conn.secret}` };
}

export async function testConnection(conn) {
  const url = conn.url.replace(/\/+$/, "");
  try {
    const res = await fetch(`${url}/api/method/frappe.auth.get_logged_user`, {
      headers: authHeader(conn),
    });
    if (res.ok) {
      const data = await res.json();
      return { ok: true, message: `Connected as ${data.message}` };
    }
    if (res.status === 0 || res.type === "opaque") {
      return { ok: false, message: "Blocked before reaching the server - likely CORS. Add allow_cors to the site's site_config.json (see README)." };
    }
    return { ok: false, message: `Site responded ${res.status} ${res.statusText} - check the API key/secret.` };
  } catch (e) {
    return { ok: false, message: `Could not reach ${url} - likely CORS or the URL is wrong. Add allow_cors to the site's site_config.json (see README). (${e.message})` };
  }
}

export function initConnectModal(ERP) {
  const modal = document.getElementById("connect-modal");
  const urlInput = document.getElementById("conn-url");
  const keyInput = document.getElementById("conn-key");
  const secretInput = document.getElementById("conn-secret");
  const status = document.getElementById("conn-status");

  function load() {
    const conn = getConnection();
    urlInput.value = conn.url || "";
    keyInput.value = conn.key || "";
    secretInput.value = conn.secret || "";
    status.textContent = "";
    status.className = "conn-status";
  }

  const sessionNote = document.getElementById("conn-session");
  const paintSession = () => {
    const st = ERP.status();
    sessionNote.hidden = st.mode !== "session";
    sessionNote.textContent = st.mode === "session" ? `Already connected: you're signed in to ${st.label} as ${st.user}, so this page reads data from it directly. You only need the fields below to push a profile to a different site.` : "";
    if (st.mode !== "session" && st.error) { status.textContent = st.error; status.className = "conn-status err"; }
  };
  const open = () => { load(); paintSession(); modal.hidden = false; };
  document.getElementById("btn-connect").addEventListener("click", open);
  document.getElementById("erp-status").addEventListener("click", open);
  document.getElementById("conn-close").addEventListener("click", () => { modal.hidden = true; });
  document.getElementById("conn-save").addEventListener("click", () => {
    setConnection({ url: urlInput.value.trim(), key: keyInput.value.trim(), secret: secretInput.value.trim() });
    status.textContent = "Connecting...";
    status.className = "conn-status";
    ERP.useToken().then((st) => {
      if (st.mode === "token") modal.hidden = true;
      else if (urlInput.value.trim()) { status.textContent = st.error || "Could not connect."; status.className = "conn-status err"; }
      else modal.hidden = true;
    });
  });
  document.getElementById("conn-test").addEventListener("click", async () => {
    status.textContent = "Testing...";
    status.className = "conn-status";
    const conn = { url: urlInput.value.trim(), key: keyInput.value.trim(), secret: secretInput.value.trim() };
    const result = await testConnection(conn);
    status.textContent = result.message;
    status.className = "conn-status " + (result.ok ? "ok" : "err");
  });
}

export { authHeader };
