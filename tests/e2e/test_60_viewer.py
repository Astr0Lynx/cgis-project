"""Diagnostic viewer is served through the gateway and is wired to the real endpoints."""

from __future__ import annotations

from conftest import GATEWAY


def test_root_redirects_to_viewer(http):
    resp = http.get(f"{GATEWAY}/")
    assert resp.status_code == 302 and resp.headers["location"] == f"{GATEWAY}/viewer/"


def test_prefix_redirects_keep_client_host_and_port(http):
    # Absolute, built from the client's Host header: QGIS's OAPIF provider needs this for ".../ogc".
    for prefix in ("ogc", "tiles", "api", "viewer"):
        resp = http.get(f"{GATEWAY}/{prefix}")
        assert resp.status_code == 301 and resp.headers["location"] == f"{GATEWAY}/{prefix}/"


def test_viewer_page_and_assets(http):
    page = http.get(f"{GATEWAY}/viewer/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    html = page.text
    assert "maplibre-gl@4.7.1" in html and 'integrity="sha384-' in html
    assert "SYNTHETIC" in html
    for asset, ctype in (("app.js", "javascript"), ("style.css", "text/css")):
        resp = http.get(f"{GATEWAY}/viewer/{asset}")
        assert resp.status_code == 200 and ctype in resp.headers["content-type"]


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
