# Shared constants, helpers and fixtures for the end-to-end suite. It runs in the Compose network
# (service e2e, profile e2e) and talks to the stack only through the gateway; PostgreSQL is read
# directly (read-only) to inspect the catalog. Expected values follow the fixture spec in README.md.

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import psycopg
import pytest

GATEWAY = os.environ.get("CGIS_GATEWAY_URL", "http://gateway:8080").rstrip("/")
PUBLIC = os.environ.get("CGIS_PUBLIC_BASE_URL", GATEWAY).rstrip("/")
OGC = f"{GATEWAY}/ogc"
TILES = f"{GATEWAY}/tiles"
API = f"{GATEWAY}/api"
FIXTURES = Path(os.environ.get("CGIS_FIXTURES_DIR", "/data/fixtures"))
STATE = Path(os.environ.get("CGIS_E2E_STATE_DIR", "/data/fixtures/e2e"))

RASTER_2001 = "synthetic-worldpop-2001"
RASTER_2011 = "synthetic-worldpop-2011"
TOWNS = "synthetic-towns"
RASTERS = {2001: RASTER_2001, 2011: RASTER_2011}


# GeoJSON rectangle polygon from its bounds. Used for TOWN_A / TOWN_B below.
def rect(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


# Fixture geometry and known zonal answers (fixture spec in README.md).
TOWN_A = rect(78.010, 17.980, 78.020, 17.990)
TOWN_B = rect(78.030, 17.950, 78.035, 17.955)
EXPECTED = {
    # (aoi, year): (valid pixels, sum, mean)
    ("town-a", 2001): (100, 100, 1),
    ("town-a", 2011): (100, 200, 2),
    ("town-b", 2001): (25, 25, 1),
    ("town-b", 2011): (25, 50, 2),
}
TOWN_GEOMS = {"town-a": TOWN_A, "town-b": TOWN_B}


# Rewrites a public link (CGIS_PUBLIC_BASE_URL) to the in-network gateway address. Used by test_20_ogc_features.py.
def internal(url: str) -> str:
    if url.startswith(PUBLIC):
        return GATEWAY + url[len(PUBLIC):]
    return url


# One shared HTTP client (no redirect following). Used by every test that calls the gateway.
@pytest.fixture(scope="session")
def http():
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        yield client


# Read-only PostgreSQL session (libpq env vars). Used by test_10_catalog.py, test_50_negative.py, test_70_persistence.py.
@pytest.fixture(scope="session")
def db():
    conn = psycopg.connect("", autocommit=True, application_name="cgis_e2e")
    conn.execute("SET default_transaction_read_only = on")
    yield conn
    conn.close()


# Directory for files shared between runs (clip, perf.json). Used by test_40_processing.py and test_80_performance.py.
@pytest.fixture(scope="session")
def state_dir() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE


# Returns `error.code` of the contract error shape, or None if the body lacks it.
# Used by test_30_tiles.py, test_40_processing.py, test_50_negative.py, test_60_gateway_viewer.py.
def error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) and isinstance(err.get("code"), str) and isinstance(err.get("message"), str):
        return err["code"]
    return None


# Deepest tile matrix the server advertises for a raster tileset (tileMatrixSetLimits).
# Used by test_30_tiles.py, test_70_persistence.py, test_80_performance.py.
def ogc_max_zoom(http: httpx.Client, dataset: str, tms: str = "WebMercatorQuad") -> int:
    meta = http.get(f"{OGC}/collections/{dataset}/tiles/{tms}", params={"f": "json"}).json()
    return max(int(lim["tileMatrix"]) for lim in meta["tileMatrixSetLimits"])


# POSTs a JSON body to the processing API. Used by test_40_processing.py, test_50_negative.py, test_70_persistence.py.
def post_json(http: httpx.Client, path: str, payload) -> httpx.Response:
    return http.post(f"{API}{path}", json=payload)
