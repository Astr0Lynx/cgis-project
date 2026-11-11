"""The PROPOSED gateway config (deploy/gateway/default.conf) with a real nginx in front of the real app (uvicorn)
and echo stubs for ogc/tiles/processing/viewer. Needs APP_TEST_NGINX=/path/to/nginx; skipped otherwise.

Proves: route split /api/exports|catalog|app -> app-api while /api/operations stays on processing; large
uploads pass the gateway (no 1m cap) yet the service limit applies; read-only routes; dot-segment refusal;
X-Real-IP cannot be spoofed; downloads stream with Range through the gateway."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

import synthetic
from conftest import APP_ROOT, PASSWORD_ADMIN, PASSWORD_USER, REPO

pytestmark = [pytest.mark.db, pytest.mark.gateway]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"{url} did not come up")


@pytest.fixture
def gateway(db, users, roots, tmp_path):
    yield from _gateway(db, roots, tmp_path, "default.conf")


@pytest.fixture
def protected_gateway(db, users, roots, tmp_path):
    yield from _gateway(db, roots, tmp_path, "default.protected.conf")


def _gateway(db, roots, tmp_path, conf_name):
    nginx = os.environ.get("APP_TEST_NGINX")
    if not nginx:
        pytest.skip("APP_TEST_NGINX not set")
    procs = []
    ports = {name: _free_port() for name in ("ogc", "tiles", "processing", "viewer", "app", "gw")}
    base = f"http://127.0.0.1:{ports['gw']}"
    try:
        for name in ("ogc", "tiles", "processing", "viewer"):
            procs.append(subprocess.Popen([sys.executable, str(REPO / "infra/tests/echo_server.py")],
                                          env={**os.environ, "MOCK_NAME": name, "MOCK_PORT": str(ports[name])}))
        env = {**os.environ, "APP_DATABASE_URL": db.api_dsn, "APP_STAGING_ROOT": str(roots["staging"]),
               "APP_ASSET_ROOT": str(roots["assets"]), "APP_EXPORT_ROOT": str(roots["exports"]),
               "APP_PUBLIC_BASE_URL": base, "APP_TRUST_PROXY_HEADERS": "true", "APP_MAX_UPLOAD_BYTES": str(6 * 2**20),
               "APP_MIN_FREE_BYTES": str(2**20), "APP_LOG_LEVEL": "WARNING", "APP_LOGIN_MAX_FAILURES": "3",
               "PYTHONPATH": os.pathsep.join([str(APP_ROOT / "src"), str(REPO / "spatial-processing/src")])}
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "cgis_app.app:app_factory",
                                       "--host", "127.0.0.1", "--port", str(ports["app"]), "--no-access-log"], env=env))
        conf = (APP_ROOT / "deploy/gateway" / conf_name).read_text()
        for host, port in (("ogc:5000", "ogc"), ("tiles:8000", "tiles"), ("processing:8000", "processing"),
                           ("viewer:8080", "viewer"), ("app-api:8000", "app")):
            assert f"server {host};" in conf
            conf = conf.replace(f"server {host};", f"server 127.0.0.1:{ports[port]};")
        conf = conf.replace("listen 8080 default_server;", f"listen 127.0.0.1:{ports['gw']} default_server;")
        prefix = tmp_path / "nginx"
        (prefix / "logs").mkdir(parents=True)
        (prefix / "default.conf").write_text(conf)
        main = (REPO / "infra/gateway/nginx.conf").read_text()
        mime = Path(nginx).resolve().parent.parent / "etc" / "nginx" / "mime.types"
        if not mime.is_file():
            mime = next(Path(nginx).resolve().parent.parent.rglob("mime.types"))
        temps = "".join(f"    {d}_temp_path {prefix}/{d}_temp;\n" for d in ("client_body", "proxy", "fastcgi", "uwsgi",
                                                                              "scgi"))
        main = (main.replace("include /etc/nginx/mime.types;", f"include {mime};\n{temps}")
                .replace("include /etc/nginx/conf.d/*.conf;", f"include {prefix}/default.conf;")
                .replace("pid /tmp/nginx.pid;", f"pid {prefix}/nginx.pid;"))
        (prefix / "nginx.conf").write_text(main)
        test = subprocess.run([nginx, "-t", "-p", str(prefix), "-c", str(prefix / "nginx.conf")], capture_output=True,
                              text=True)
        assert test.returncode == 0, test.stderr
        procs.append(subprocess.Popen([nginx, "-p", str(prefix), "-c", str(prefix / "nginx.conf"), "-g",
                                       "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        _wait(f"http://127.0.0.1:{ports['app']}/api/app/health")
        _wait(f"{base}/healthz")
        yield base
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def _raw_status(base: str, path: str) -> int:
    """Send a request line verbatim (HTTP clients normalize '..' away before sending)."""
    port = int(base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
        head = sock.recv(64).decode("latin-1")
    return int(head.split(" ", 2)[1])


def _login(c: httpx.Client, base: str, user: str, password: str) -> str:
    r = c.post(f"{base}/auth/login", json={"username": user, "password": password}, headers={"Origin": base})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def test_routing_split(gateway):
    with httpx.Client() as c:
        assert c.get(f"{gateway}/auth/me").json()["authenticated"] is False
        assert c.get(f"{gateway}/api/exports/capabilities").json()["kinds"] == ["full", "clip"]
        assert "paths" in c.get(f"{gateway}/api/app/openapi.json").json()
        assert c.get(f"{gateway}/api/catalog/datasets").json()["total"] == 0
        echo = c.post(f"{gateway}/api/operations/clip", json={}).json()
        assert echo["service"] == "processing" and echo["path"] == "/operations/clip"
        assert c.get(f"{gateway}/api/downloads/x").json()["service"] == "processing"
        assert c.get(f"{gateway}/ogc/collections").json()["service"] == "ogc"
        assert c.get(f"{gateway}/admin/datasets").status_code == 401
        assert c.post(f"{gateway}/admin/datasets/uploads").status_code == 401
        assert c.post(f"{gateway}/api/catalog/datasets").status_code == 403  # read-only at the gateway
        for raw in ("/admin/../auth/me", "/admin/%2e%2e/auth/me", "/api/exports/../../admin/datasets"):
            assert _raw_status(gateway, raw) == 400, raw  # dot segments refused before routing


def test_upload_publish_and_stream_download_through_gateway(gateway, tmp_path, run_ingest):
    import hashlib

    import numpy as np
    import rasterio

    path = tmp_path / "three_mb.tif"
    rng = np.random.default_rng(1)
    with rasterio.open(path, "w", driver="GTiff", width=900, height=900, count=1, dtype="float32", crs="EPSG:4326",
                       transform=rasterio.transform.from_origin(78, 18, 0.0001, 0.0001), nodata=-1.0) as dst:
        dst.write(rng.random((900, 900), dtype=np.float32), 1)
    assert path.stat().st_size > 3 * 2**20  # well above the 1m cap of the /api/ location
    with httpx.Client(timeout=60) as c:
        csrf = _login(c, gateway, "alice-admin", PASSWORD_ADMIN)
        hdrs = {"X-CSRF-Token": csrf, "Origin": gateway}
        meta = synthetic.raster_meta("gw-pop", 1)
        with open(path, "rb") as fh:
            r = c.post(f"{gateway}/admin/datasets/uploads", headers=hdrs,
                       files={"file": (path.name, fh, "image/tiff"), "metadata": (None, json.dumps(meta))})
        assert r.status_code == 202, r.text
        run_ingest()
        assert c.get(f"{gateway}/admin/jobs/{r.json()['job_id']}").json()["status"] == "completed"
        r = c.post(f"{gateway}/admin/datasets/gw-pop/versions/1/publish", headers=hdrs)
        assert r.status_code == 202
        run_ingest()
        assert c.get(f"{gateway}/admin/jobs/{r.json()['job_id']}").json()["status"] == "completed"

        big = tmp_path / "too_big.tif"
        with open(big, "wb") as fh:
            fh.write(b"II*\x00")
            fh.seek(7 * 2**20)
            fh.write(b"\x00")
        with open(big, "rb") as fh:
            r = c.post(f"{gateway}/admin/datasets/uploads", headers=hdrs,
                       files={"file": (big.name, fh, "image/tiff"), "metadata": (None, json.dumps(meta))})
        assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"

        job = c.post(f"{gateway}/api/exports", json={"dataset_id": "gw-pop", "kind": "full", "format": "COG"},
                     headers=hdrs).json()
        url = f"{gateway}{job['links']['download']}"
        h = hashlib.sha256()
        with c.stream("GET", url) as resp:
            assert resp.status_code == 200 and resp.headers["x-checksum-sha256"] == job["result"]["sha256"]
            for chunk in resp.iter_bytes():
                h.update(chunk)
        assert h.hexdigest() == job["result"]["sha256"]
        part = c.get(url, headers={"Range": "bytes=10-19"})
        assert part.status_code == 206 and len(part.content) == 10


def test_x_real_ip_is_set_by_the_gateway(gateway, db):
    with httpx.Client() as c:
        for _ in range(2):
            c.post(f"{gateway}/auth/login", json={"username": "bob-user", "password": "wrong-password-1"},
                   headers={"X-Real-IP": "203.0.113.9"})
    with db.owner() as conn:
        keys = {r["key"] for r in conn.execute("SELECT key FROM cgis_app.login_attempts").fetchall()}
    assert "ip:127.0.0.1" in keys and "ip:203.0.113.9" not in keys


def test_user_session_through_gateway(gateway):
    with httpx.Client() as c:
        csrf = _login(c, gateway, "bob-user", PASSWORD_USER)
        assert c.get(f"{gateway}/admin/datasets").status_code == 403
        assert c.get(f"{gateway}/api/exports").status_code == 200
        r = c.post(f"{gateway}/api/exports", json={"dataset_id": "nope", "kind": "full", "format": "COG"})
        assert r.status_code == 403  # no CSRF header
        r = c.post(f"{gateway}/api/exports", json={"dataset_id": "nope", "kind": "full", "format": "COG"},
                   headers={"X-CSRF-Token": csrf, "Origin": gateway})
        assert r.status_code == 404
        assert c.post(f"{gateway}/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert c.get(f"{gateway}/api/exports").status_code == 401


def test_protected_variant_gates_read_routes(protected_gateway):
    base = protected_gateway
    with httpx.Client() as anon:
        r = anon.get(f"{base}/ogc/collections")
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
        assert r.headers["www-authenticate"].startswith("Bearer")
        assert anon.post(f"{base}/api/operations/clip", json={}).status_code == 401
        assert anon.get(f"{base}/tiles/datasets").status_code == 401
        assert anon.get(f"{base}/auth/me").status_code == 200          # login itself stays reachable
        assert anon.get(f"{base}/healthz").status_code == 200
    with httpx.Client() as c:
        csrf = _login(c, base, "bob-user", PASSWORD_USER)
        assert c.get(f"{base}/ogc/collections").json()["service"] == "ogc"
        assert c.get(f"{base}/tiles/datasets").json()["service"] == "tiles"
        r = c.post(f"{base}/api/operations/clip", json={})          # cookie session, no CSRF header
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
        echo = c.post(f"{base}/api/operations/clip", json={}, headers={"X-CSRF-Token": csrf}).json()
        assert echo["service"] == "processing"
        pat = c.post(f"{base}/auth/tokens", json={"name": "qgis"}, headers={"X-CSRF-Token": csrf}).json()["token"]
    with httpx.Client() as qgis:  # QGIS/GDAL style: HTTP Basic with a personal access token as the password
        assert qgis.get(f"{base}/ogc/collections", auth=("anything", pat)).json()["service"] == "ogc"
        assert qgis.get(f"{base}/ogc/collections", auth=("bob-user", PASSWORD_USER)).status_code == 401
