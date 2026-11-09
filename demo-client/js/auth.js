// Session handling against Agent 5's /auth API (provisional pin: docs/openapi.agent5.provisional.json).
//
// The UI hides controls a role may not use, but every permission is enforced by the backend; a 401
// anywhere (session expired) or 403 (wrong role) is surfaced, never worked around.

import { $, h, icon, replace, toast } from "./dom.js";
import { request, setCsrfToken, explain, toError } from "./http.js";

export function createAuth({ config, store, onChange }) {
  const app = config.applicationApi || {};
  const enabled = Boolean(app.enabled);
  const base = app.auth || "/auth";
  let expiryTimer = 0;
  let pendingRetry = null;

  function setSession(session, reason) {
    const user = session && session.authenticated ? session.user : null;
    setCsrfToken(session && session.csrf_token);
    store.set({ session: { status: user ? "authenticated" : enabled ? "anonymous" : "unavailable", user, expiresAt: session?.expires_at || null } });
    clearTimeout(expiryTimer);
    if (user && session.expires_at) {
      const ms = Date.parse(session.expires_at) - Date.now();
      if (ms > 0 && ms < 2 ** 31) expiryTimer = setTimeout(() => expired(), ms + 500);
    }
    renderAccount();
    onChange?.(reason);
  }

  async function refresh() {
    if (!enabled) { setSession(null, "init"); return; }
    try {
      const res = await request(`${base}/me`);
      if (res.ok) setSession(res.body, "init");
      else setSession(null, "init");
    } catch {
      store.set({ session: { status: "unavailable", user: null } });
      renderAccount();
      onChange?.("init");
    }
  }

  async function login(username, password) {
    const res = await request(`${base}/login`, { method: "POST", json: { username, password } });
    if (!res.ok) throw toError(res, `${base}/login`);
    setSession(res.body.authenticated === undefined ? { ...res.body, authenticated: true } : res.body, "login");
    hideBanner();
    toast(`Signed in as ${res.body.user?.username ?? username} (${res.body.user?.role ?? "user"}).`);
    if (pendingRetry) { const fn = pendingRetry; pendingRetry = null; fn(); }
  }

  async function logout() {
    try { await request(`${base}/logout`, { method: "POST" }); } catch { /* the session is dropped locally either way */ }
    setSession({ authenticated: false }, "logout");
    toast("Signed out.");
  }

  function expired() {
    if (store.get().session?.status !== "authenticated") return;
    setSession({ authenticated: false }, "expired");
    showBanner("Your session has expired. Sign in again to continue; nothing was changed while signed out.", "Sign in");
  }

  // Any 401 from a protected route while we believe we are signed in == expiry.
  window.addEventListener("cgis:unauthorized", (e) => {
    if (!enabled) return;
    const url = e.detail?.url || "";
    if (url.endsWith("/login")) return;
    if (store.get().session?.status === "authenticated") expired();
  });

  function showBanner(text, actionLabel) {
    const banner = $("banner");
    const btn = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => openLogin() }, actionLabel);
    replace(banner, icon("lock"), h("span", { text }), h("span", { class: "spacer" }), btn);
    banner.hidden = false;
  }
  function hideBanner() { $("banner").hidden = true; }

  // ---- login dialog ----
  const dialog = $("login-dialog");
  const form = $("login-form");
  const err = $("login-error");
  function openLogin(reason, retry) {
    if (!enabled) return;
    pendingRetry = retry || null;
    $("login-reason").textContent = reason || "Administrators can upload and publish datasets. Users can view, analyse and export.";
    err.hidden = true;
    form.reset();
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    $("login-username").focus();
  }
  const close = () => { pendingRetry = null; dialog.close(); };
  $("login-cancel").addEventListener("click", close);
  $("login-cancel-x").addEventListener("click", close);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = $("login-username").value.trim();
    const password = $("login-password").value;
    for (const [id, ok] of [["login-username", Boolean(username)], ["login-password", Boolean(password)]]) {
      $(id).setAttribute("aria-invalid", ok ? "false" : "true");
    }
    if (!username || !password) {
      err.textContent = "Enter a username and password.";
      err.hidden = false;
      return;
    }
    const submit = $("login-submit");
    submit.disabled = true;
    submit.textContent = "Signing in…";
    try {
      await login(username, password);
      dialog.close();
    } catch (ex) {
      err.textContent = explain(ex);
      err.hidden = false;
      $("login-password").select();
    } finally {
      submit.disabled = false;
      submit.textContent = "Sign in";
    }
  });

  function renderAccount() {
    const host = $("account");
    const s = store.get().session || {};
    if (!enabled || s.status === "unavailable") {
      replace(host, h("span", { class: "muted", title: "The application API (accounts, admin, exports) is not enabled on this deployment" }, "Accounts not enabled"));
      $("nav-admin").hidden = true;
      return;
    }
    if (s.status === "authenticated" && s.user) {
      replace(host,
        h("div", { class: "who" }, h("strong", { text: s.user.username }), h("span", { text: s.user.role === "admin" ? "Administrator" : "Read-only user" })),
        h("button", { class: "btn btn-ghost btn-sm", type: "button", id: "logout-btn", onclick: () => logout() }, "Sign out"));
    } else {
      replace(host, h("button", { class: "btn btn-ghost btn-sm", type: "button", id: "login-btn", onclick: () => openLogin() }, icon("lock"), "Sign in"));
    }
    $("nav-admin").hidden = !(s.status === "authenticated" && s.user?.role === "admin");
  }

  return {
    enabled,
    refresh,
    login,
    logout,
    openLogin,
    isAdmin: () => store.get().session?.user?.role === "admin",
    isSignedIn: () => store.get().session?.status === "authenticated",
    expired,
  };
}
