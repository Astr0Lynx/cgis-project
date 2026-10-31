"""Shared fixtures for the CyberGIS Phase 2 end-to-end suite.

The suite runs inside the Compose network (see compose.e2e.yaml) and talks to the stack only
through the public gateway, as a client would. It reads PostgreSQL directly only to inspect the
catalog (read-only session).

Expected values come from contracts/SYNTHETIC_DATA.md. Dataset ids are referenced here, in the
tests, never in service code.
"""

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


def rect(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


# contracts/SYNTHETIC_DATA.md
TOWN_A = rect(78.010, 17.980, 78.020, 17.990)
TOWN_B = rect(78.030, 17.950, 78.035, 17.955)
RASTER_BOUNDS = (78.000, 17.936, 78.064, 18.000)
EXPECTED = {
    # (aoi, year): (valid pixels, sum, mean)
    ("town-a", 2001): (100, 100, 1),
    ("town-a", 2011): (100, 200, 2),
    ("town-b", 2001): (25, 25, 1),
    ("town-b", 2011): (25, 50, 2),
}
TOWN_GEOMS = {"town-a": TOWN_A, "town-b": TOWN_B}


def internal(url: str) -> str:
    """Rewrite a public link (PUBLIC_BASE_URL) to the in-network gateway address."""
    if url.startswith(PUBLIC):
        return GATEWAY + url[len(PUBLIC):]
    return url


@pytest.fixture(scope="session")
def http():
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="session")
def db():
    conn = psycopg.connect("", autocommit=True, application_name="cgis_e2e")
    conn.execute("SET default_transaction_read_only = on")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def state_dir() -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE


def error_code(resp: httpx.Response) -> str | None:
    """`error.code` of the contract error shape, or None if the body does not have it."""
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict) and isinstance(err.get("code"), str) and isinstance(err.get("message"), str):
        return err["code"]
    return None


def ogc_max_zoom(http: httpx.Client, dataset: str, tms: str = "WebMercatorQuad") -> int:
    """Deepest tile matrix the server advertises for a raster tileset (tileMatrixSetLimits)."""
    meta = http.get(f"{OGC}/collections/{dataset}/tiles/{tms}", params={"f": "json"}).json()
    return max(int(lim["tileMatrix"]) for lim in meta["tileMatrixSetLimits"])


def post_json(http: httpx.Client, path: str, payload) -> httpx.Response:
    return http.post(f"{API}{path}", json=payload)
