# Authentication: password login, cookie/CSRF, bearer and personal access tokens, expiry, revocation,
# throttling, and that no secret reaches the logs.

from __future__ import annotations

import base64
import logging

import pytest

from conftest import PASSWORD_ADMIN, PASSWORD_USER, login

pytestmark = pytest.mark.db


# All Set-Cookie headers of a response joined.
def _cookie_header(resp) -> str:
    return "; ".join(v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie")


# Verifies: login sets hardened cookie and returns role.
def test_login_sets_hardened_cookie_and_returns_role(anon_client, users):
    r = anon_client.post("/auth/login", json={"username": "Alice-Admin", "password": PASSWORD_ADMIN})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True and body["user"]["role"] == "admin" and body["user"]["username"] == "alice-admin"
    assert body["csrf_token"] and body["auth_method"] == "cookie" and body.get("access_token") is None
    cookie = _cookie_header(r)
    assert "cgis_session=cgis_s_" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie.replace(
        "Strict", "strict") and "Path=/" in cookie
    assert r.headers["cache-control"] == "no-store"
    me = anon_client.get("/auth/me").json()
    assert me["authenticated"] and me["user"]["role"] == "admin" and me["csrf_token"] == body["csrf_token"]


# Verifies: anonymous me is 200.
def test_anonymous_me_is_200(anon_client):
    r = anon_client.get("/auth/me")
    body = r.json()
    assert r.status_code == 200 and body["authenticated"] is False
    assert body["user"] is None and body["expires_at"] is None and body["csrf_token"] is None


# Verifies: bad credentials are indistinguishable.
def test_bad_credentials_are_indistinguishable(anon_client, users):
    a = anon_client.post("/auth/login", json={"username": "alice-admin", "password": "not-the-password-1"})
    b = anon_client.post("/auth/login", json={"username": "nobody-here", "password": "not-the-password-1"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"error": {"code": "INVALID_CREDENTIALS",
                                              "message": "unknown username or wrong password"}}
    assert "set-cookie" not in a.headers


# Verifies: lockout after repeated failures.
def test_lockout_after_repeated_failures(anon_client, users):
    for _ in range(3):  # login_max_failures=3 in the test settings
        assert anon_client.post("/auth/login", json={"username": "bob-user", "password": "wrong-wrong-1"}
                                ).status_code == 401
    r = anon_client.post("/auth/login", json={"username": "bob-user", "password": PASSWORD_USER})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert int(r.headers["retry-after"]) > 0
    # other accounts are unaffected
    assert anon_client.post("/auth/login", json={"username": "alice-admin", "password": PASSWORD_ADMIN}
                            ).status_code == 200


# Verifies: logout revokes and needs CSRF.
def test_logout_revokes_and_needs_csrf(make_client, users):
    c = make_client()
    csrf = login(c, users["user"])
    stolen = c.cookies.get("cgis_session")
    assert c.post("/auth/logout").status_code == 403  # no X-CSRF-Token
    r = c.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204 and "cgis_session=" in _cookie_header(r)
    assert c.post("/auth/logout").status_code == 204  # idempotent once signed out
    replay = make_client()
    replay.cookies.set("cgis_session", stolen)
    me = replay.get("/auth/me")
    assert me.status_code == 200 and me.json()["authenticated"] is False and me.json()["reason"] == "SESSION_REVOKED"
    replay.cookies.set("cgis_session", stolen)
    r = replay.get("/api/exports")
    assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REVOKED"


# Verifies: CSRF and origin enforced for cookie sessions.
def test_csrf_and_origin_enforced_for_cookie_sessions(user_client):
    token = user_client.headers.pop("X-CSRF-Token")
    body = {"dataset_id": "nope", "kind": "full", "format": "COG"}
    r = user_client.post("/api/exports", json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = user_client.post("/api/exports", json=body, headers={"X-CSRF-Token": "x" * 43})
    assert r.json()["error"]["code"] == "CSRF_FAILED"
    r = user_client.post("/api/exports", json=body, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGIN_FORBIDDEN"
    r = user_client.post("/api/exports", json=body, headers={"X-CSRF-Token": token, "Sec-Fetch-Site": "cross-site"})
    assert r.json()["error"]["code"] == "ORIGIN_FORBIDDEN"
    r = user_client.post("/api/exports", json=body, headers={"X-CSRF-Token": token, "Origin": "http://testserver"})
    assert r.status_code == 404  # authenticated and allowed; the dataset just does not exist


# Verifies: login CSRF from foreign origin refused.
def test_login_csrf_from_foreign_origin_refused(anon_client, users):
    r = anon_client.post("/auth/login", json={"username": "bob-user", "password": PASSWORD_USER},
                         headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGIN_FORBIDDEN"


# Verifies: login requires json.
def test_login_requires_json(anon_client, users):
    r = anon_client.post("/auth/login", data={"username": "bob-user", "password": PASSWORD_USER})
    assert r.status_code == 422


# Verifies: session expiry and idle timeout.
def test_session_expiry_and_idle_timeout(db, make_client, users):
    c = make_client()
    login(c, users["user"])
    with db.owner() as conn:
        conn.execute("UPDATE cgis_app.sessions SET expires_at = now() - interval '1 second' + interval '0', "
                     "created_at = now() - interval '2 hours'")
    r = c.get("/api/exports")
    assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_EXPIRED"
    assert r.headers["www-authenticate"].startswith("Bearer")
    me = c.get("/auth/me")
    assert me.status_code == 200 and me.json()["reason"] == "SESSION_EXPIRED"

    c2 = make_client()
    login(c2, users["user"])
    with db.owner() as conn:
        conn.execute("UPDATE cgis_app.sessions SET last_seen_at = now() - interval '2 hours', "
                     "created_at = now() - interval '3 hours' WHERE revoked_at IS NULL AND expires_at > now()")
    r = c2.get("/api/exports")
    assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_EXPIRED"


# Verifies: bearer login for scripts.
def test_bearer_login_for_scripts(anon_client, make_client, users):
    r = anon_client.post("/auth/login", json={"username": "bob-user", "password": PASSWORD_USER, "token": True})
    assert r.status_code == 200 and "set-cookie" not in r.headers
    token = r.json()["access_token"]
    assert token.startswith("cgis_b_") and r.json()["token_type"] == "Bearer"
    c = make_client()
    me = c.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["authenticated"] and me["auth_method"] == "bearer" and me["csrf_token"] is None
    # bearer is not ambient: no CSRF token needed for POST
    r = c.post("/api/exports", json={"dataset_id": "nope", "kind": "full", "format": "COG"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
    assert c.post("/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    r = c.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REVOKED"


# Verifies: credentials only valid in their channel.
def test_credentials_only_valid_in_their_channel(make_client, users):
    c = make_client()
    login(c, users["user"])
    browser_secret = c.cookies.get("cgis_session")
    other = make_client()
    r = other.get("/auth/me", headers={"Authorization": f"Bearer {browser_secret}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
    tok = c.post("/auth/login", json={"username": "bob-user", "password": PASSWORD_USER, "token": True}
                 ).json()["access_token"]
    third = make_client()
    third.cookies.set("cgis_session", tok)
    assert third.get("/api/exports").status_code == 401
    for bad in ("Bearer", "Bearer not-a-token", "Token abc", "Digest x"):
        assert other.get("/api/exports", headers={"Authorization": bad}).status_code == 401


# Verifies: personal access tokens.
def test_personal_access_tokens(admin_client, make_client, users):
    r = admin_client.post("/auth/tokens", json={"name": "QGIS desktop", "expires_in_days": 7})
    assert r.status_code == 201, r.text
    created = r.json()
    pat = created["token"]
    assert pat.startswith("cgis_pat_") and created["scope"] == "read"
    listed = admin_client.get("/auth/tokens").json()
    assert [t["token_id"] for t in listed] == [created["token_id"]] and "token" not in listed[0]

    qgis = make_client()
    me = qgis.get("/auth/me", headers={"Authorization": f"Bearer {pat}"}).json()
    assert me["user"]["role"] == "user" and me["auth_method"] == "token"  # read scope demotes the admin
    assert qgis.get("/admin/datasets", headers={"Authorization": f"Bearer {pat}"}).status_code == 403
    basic = base64.b64encode(f"alice-admin:{pat}".encode()).decode()
    assert qgis.get("/api/exports", headers={"Authorization": f"Basic {basic}"}).status_code == 200
    # a PAT cannot mint more tokens or change the password
    assert qgis.post("/auth/tokens", json={"name": "x"}, headers={"Authorization": f"Bearer {pat}"}).status_code == 403

    adm = admin_client.post("/auth/tokens", json={"name": "admin automation", "scope": "admin"}).json()["token"]
    assert qgis.get("/admin/datasets", headers={"Authorization": f"Bearer {adm}"}).status_code == 200

    assert admin_client.delete(f"/auth/tokens/{created['token_id']}").status_code == 204
    r = qgis.get("/api/exports", headers={"Authorization": f"Bearer {pat}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_REVOKED"
    assert admin_client.delete(f"/auth/tokens/{created['token_id']}").status_code == 404


# Verifies: user cannot create admin scoped token.
def test_user_cannot_create_admin_scoped_token(user_client):
    r = user_client.post("/auth/tokens", json={"name": "escalate", "scope": "admin"})
    assert r.status_code == 403
    r = user_client.post("/auth/tokens", json={"name": "x", "role": "admin"})
    assert r.status_code == 422  # unknown fields are refused


# Verifies: basic auth never accepts passwords.
def test_basic_auth_never_accepts_passwords(anon_client, users):
    basic = base64.b64encode(f"bob-user:{PASSWORD_USER}".encode()).decode()
    r = anon_client.get("/api/exports", headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


# Verifies: secrets in URL refused.
@pytest.mark.parametrize("query", ["access_token=abc", "token=abc", "password=x", "api_key=1", "TOKEN=x"])
def test_secrets_in_url_refused(anon_client, query):
    r = anon_client.get(f"/api/catalog/datasets?{query}")
    assert r.status_code == 400 and r.json()["error"]["code"] == "SECRET_IN_URL"


# Verifies: password change revokes other sessions.
def test_password_change_revokes_other_sessions(make_client, users):
    a, b = make_client(), make_client()
    csrf = login(a, users["user"])
    login(b, users["user"])
    tok = a.post("/auth/tokens", json={"name": "t"}, headers={"X-CSRF-Token": csrf}).json()["token"]
    r = a.post("/auth/password", json={"current_password": "wrong-password-1", "new_password": "N3w-Strong-pass!"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401
    r = a.post("/auth/password", json={"current_password": PASSWORD_USER, "new_password": "short"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEAK_PASSWORD"
    r = a.post("/auth/password", json={"current_password": PASSWORD_USER, "new_password": "N3w-Strong-pass!"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert a.get("/auth/me").json()["authenticated"] is True  # this session survives
    assert b.get("/api/exports").status_code == 401  # the other one does not
    assert make_client().get("/api/exports", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    c = make_client()
    assert c.post("/auth/login", json={"username": "bob-user", "password": PASSWORD_USER}).status_code == 401
    assert c.post("/auth/login", json={"username": "bob-user", "password": "N3w-Strong-pass!"}).status_code == 200


# Verifies: disabled account.
def test_disabled_account(db, settings, make_client, users):
    from cgis_app import identity
    from cgis_app.db import connect

    c = make_client()
    login(c, users["user"])
    with connect(db.api_dsn, application_name="t") as conn:
        identity.set_disabled(conn, identity.get_user(conn, "bob-user"), True)
    assert c.get("/api/exports").status_code == 401
    r = make_client().post("/auth/login", json={"username": "bob-user", "password": PASSWORD_USER})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_DISABLED"


# Verifies: role change revokes sessions.
def test_role_change_revokes_sessions(db, make_client, users):
    from cgis_app import identity
    from cgis_app.db import connect

    c = make_client()
    login(c, users["admin"])
    assert c.get("/admin/datasets").status_code == 200
    with connect(db.api_dsn, application_name="t") as conn:
        identity.set_role(conn, identity.get_user(conn, "alice-admin"), "user")
    assert c.get("/admin/datasets").status_code == 401


# Verifies: passwords stored hashed.
def test_passwords_stored_hashed(db, users):
    with db.owner() as conn:
        rows = conn.execute("SELECT password_hash FROM cgis_app.users").fetchall()
        sessions = conn.execute("SELECT token_hash FROM cgis_app.sessions").fetchall()
    assert rows and all(r["password_hash"].startswith("$argon2id$") for r in rows)
    assert all(PASSWORD_USER not in r["password_hash"] for r in rows)
    with db.owner() as conn:
        with pytest.raises(Exception):
            conn.execute("INSERT INTO cgis_app.users (username, role, password_hash) VALUES ('x1', 'user', 'plain')")
    assert sessions == [] or all(len(bytes(s["token_hash"])) == 32 for s in sessions)


# Verifies: no secrets in logs.
def test_no_secrets_in_logs(caplog, make_client, users):
    caplog.set_level(logging.DEBUG)
    c = make_client()
    csrf = login(c, users["admin"])
    session = c.cookies.get("cgis_session")
    pat = c.post("/auth/tokens", json={"name": "log-check"}, headers={"X-CSRF-Token": csrf}).json()["token"]
    make_client().get("/api/exports", headers={"Authorization": f"Bearer {pat}"})
    make_client().post("/auth/login", json={"username": "alice-admin", "password": "wrong-and-secret-xyz"})
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "login ok user=alice-admin" in text
    for secret in (PASSWORD_ADMIN, "wrong-and-secret-xyz", session, pat, csrf):
        assert secret not in text


# Verifies: gateway verify.
def test_gateway_verify(make_client, users):
    anon = make_client()
    assert anon.get("/auth/verify").status_code == 401
    u = make_client()
    csrf = login(u, users["user"])
    r = u.get("/auth/verify")
    assert r.status_code == 204 and r.headers["x-cgis-role"] == "user" and r.headers["x-cgis-user"] == "bob-user"
    assert u.get("/auth/verify?require=admin").status_code == 403
    # a cookie-authenticated unsafe request through the gateway still needs CSRF
    assert u.get("/auth/verify", headers={"X-Original-Method": "POST"}).status_code == 403
    assert u.get("/auth/verify", headers={"X-Original-Method": "POST", "X-CSRF-Token": csrf}).status_code == 204
    a = make_client()
    login(a, users["admin"])
    assert a.get("/auth/verify?require=admin").status_code == 204
