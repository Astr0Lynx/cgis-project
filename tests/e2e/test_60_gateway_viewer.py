# Gateway routing/hardening and the diagnostic viewer it serves at /viewer/.

from __future__ import annotations

import socket

import httpx
import pytest

from conftest import GATEWAY, OGC, error_code


# The gateway root redirects to /viewer/.
def test_root_redirects_to_viewer(http):
    resp = http.get(f"{GATEWAY}/")
    assert resp.status_code == 302 and resp.headers["location"] == f"{GATEWAY}/viewer/"


# Bare prefixes redirect to "<prefix>/" with an absolute URL built from the client's Host header
# (QGIS's OAPIF provider needs this for ".../ogc").
def test_prefix_redirects_keep_client_host_and_port(http):
    for prefix in ("ogc", "tiles", "api", "viewer"):
        resp = http.get(f"{GATEWAY}/{prefix}")
        assert resp.status_code == 301 and resp.headers["location"] == f"{GATEWAY}/{prefix}/"


# The viewer page loads pinned MapLibre with SRI, says SYNTHETIC, and serves app.js and style.css.
def test_viewer_page_and_assets(http):
    page = http.get(f"{GATEWAY}/viewer/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    html = page.text
    assert "maplibre-gl@4.7.1" in html and 'integrity="sha384-' in html
    assert "SYNTHETIC" in html
    for asset, ctype in (("app.js", "javascript"), ("style.css", "text/css")):
        resp = http.get(f"{GATEWAY}/viewer/{asset}")
        assert resp.status_code == 200 and ctype in resp.headers["content-type"]


# app.js discovers datasets (no hard-coded ids) and uses the OGC and vendor tile templates and both operations.
def test_viewer_uses_discovery_not_hardcoded_ids(http):
    js = http.get(f"{GATEWAY}/viewer/app.js").text
    assert "synthetic-" not in js and "town-a" not in js
    # OGC API - Tiles template ({z}/{y}/{x}) and vendor TiTiler template are both present and labelled.
    assert "/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png" in js
    assert 'const TILES = "/tiles"' in js and "/datasets/${enc}/tiles/WebMercatorQuad/{z}/{x}/{y}.png" in js
    assert "vendor" in js
    # Zoom range comes from the server's tileMatrixSetLimits / TileJSON, not a constant.
    assert "tileMatrixSetLimits" in js and "tilejson.json" in js
    assert "/operations/zonal-statistics" in js and "/operations/clip" in js


# --- gateway routing and hardening (infra/gateway/default.conf) ---------------------------------------

# Sends a GET whose path goes out byte-for-byte (HTTP clients normalise dot-segments) and returns
# (status, body). Used by test_dot_segments_refused.
def raw_get(raw_path: str) -> tuple[int, str]:
    url = httpx.URL(GATEWAY)
    with socket.create_connection((url.host, url.port or 80), timeout=10) as sock:
        sock.sendall(f"GET {raw_path} HTTP/1.1\r\nHost: {url.netloc.decode()}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    head, _, body = data.decode("latin-1").partition("\r\n\r\n")
    return int(head.split()[1]), body


# Gateway liveness answers without touching upstreams.
def test_gateway_healthz(http):
    resp = http.get(f"{GATEWAY}/healthz")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


# Raw and percent-encoded dot-segments are refused before proxying.
@pytest.mark.parametrize("raw_path", ["/ogc/../tiles/x", "/tiles/%2e%2e/api/x", "/api/./operations/clip"])
def test_dot_segments_refused(raw_path):
    status, body = raw_get(raw_path)
    assert status == 400 and '"BAD_PATH"' in body


# Unknown routes get the JSON error shape, not an nginx HTML page.
def test_unknown_route_is_json_404(http):
    resp = http.get(f"{GATEWAY}/nope")
    assert resp.status_code == 404 and error_code(resp) == "NOT_FOUND"


# HEAD is allowed on the read-only prefixes (GET implies HEAD).
def test_head_allowed_on_read_only_prefix(http):
    assert http.head(f"{OGC}/").status_code == 200


# The Server header does not leak the nginx version.
def test_no_server_version_banner(http):
    assert "/" not in http.get(f"{GATEWAY}/healthz").headers.get("server", "")
