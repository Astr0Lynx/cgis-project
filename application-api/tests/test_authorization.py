"""Server-side authorization: every admin route refuses anonymous (401) and user (403) callers on direct HTTP,
including path and header spoofing; users may read and export."""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.db

ADMIN_ROUTES = [
    ("GET", "/admin/datasets", None),
    ("POST", "/admin/datasets", "upload"),
    ("POST", "/admin/datasets/uploads", "upload"),
    ("GET", "/admin/datasets/some-dataset", None),
    ("GET", "/admin/datasets/some-dataset/versions/1", None),
    ("POST", "/admin/datasets/some-dataset/versions/1/validate", None),
    ("POST", "/admin/datasets/some-dataset/versions/1/publish", None),
    ("DELETE", "/admin/datasets/some-dataset/versions/1", None),
    ("PUT", "/admin/datasets/some-dataset/access", {"access": "public"}),
    ("GET", "/admin/jobs", None),
    ("GET", f"/admin/jobs/{uuid.uuid4()}", None),
]

TIFF = b"II*\x00" + b"\x00" * 64


def _call(client, method, path, body):
    if body == "upload":
        return client.request(method, path, files={"file": ("x.tif", TIFF, "image/tiff"),
                                                   "metadata": (None, json.dumps({"dataset_id": "x"}))})
    if isinstance(body, dict):
        return client.request(method, path, json=body)
    return client.request(method, path)


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_anonymous_gets_401(anon_client, method, path, body):
    r = _call(anon_client, method, path, body)
    assert r.status_code == 401, (method, path, r.text)
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_user_gets_403(user_client, roots, method, path, body):
    r = _call(user_client, method, path, body)
    assert r.status_code == 403, (method, path, r.text)
    assert r.json()["error"] == {"code": "FORBIDDEN", "message": "this action requires the admin role",
                                 "details": {"role": "user", "required_role": "admin"}}
    assert list(roots["staging"].iterdir()) == []  # a refused upload never reaches staging


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_is_not_refused(admin_client, method, path, body):
    r = _call(admin_client, method, path, body)
    assert r.status_code not in (401, 403), (method, path, r.text)


@pytest.mark.parametrize("headers", [
    {"X-Role": "admin"}, {"X-Cgis-Role": "admin"}, {"X-Cgis-User": "alice-admin"},
    {"X-Forwarded-User": "alice-admin"}, {"X-Original-URL": "/auth/me"}, {"X-Rewrite-URL": "/auth/me"},
    {"X-Forwarded-Prefix": "/admin"}, {"X-HTTP-Method-Override": "GET"},
])
def test_spoofed_headers_do_not_grant_admin(user_client, headers):
    assert user_client.get("/admin/datasets", headers=headers).status_code == 403
    assert user_client.post("/admin/datasets/x/versions/1/publish", headers=headers).status_code == 403


@pytest.mark.parametrize("path", [
    "/Admin/datasets", "/ADMIN/datasets", "/admin//datasets", "/api/../admin/datasets", "/api/%2e%2e/admin/datasets",
    "/admin/datasets%2f..%2f..%2fauth/me", "/auth/../admin/datasets", "/admin/datasets/;/", "/admin/datasets/.%2e/",
])
def test_path_variants_never_expose_admin(user_client, anon_client, path):
    for client in (user_client, anon_client):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (307, 308, 400, 401, 403, 404, 422), (path, r.status_code)
        if r.status_code in (307, 308):
            target = r.headers["location"]
            assert client.get(target, follow_redirects=False).status_code in (401, 403, 404)


def test_user_cannot_escalate_through_bodies(user_client):
    r = user_client.post("/api/exports", json={"dataset_id": "x", "kind": "full", "format": "COG", "owner": "alice"})
    assert r.status_code == 422
    r = user_client.post("/auth/login", json={"username": "bob-user", "password": "x", "role": "admin"})
    assert r.status_code == 422


def test_users_may_read_and_export(user_client):
    assert user_client.get("/api/catalog/datasets").status_code == 200
    assert user_client.get("/api/exports").status_code == 200
    assert user_client.get("/api/exports/capabilities").status_code == 200


def test_method_not_allowed_is_json(user_client):
    r = user_client.request("PATCH", "/api/exports")
    assert r.status_code == 405 and r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
