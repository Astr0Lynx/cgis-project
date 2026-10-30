"""Integration fixtures: every request goes over HTTP through the gateway stub.

Run via ``docker compose -f tests/stack/compose.yaml run --rm tests``. The
catalog fixtures (SYNTHETIC) come from tests/stack/stub_catalog.sql.
"""

import io
import json
import os
import time
import uuid

import httpx
import morecantile
import psycopg
import pytest
from PIL import Image

GATEWAY = os.environ.get("CGIS_GATEWAY_URL", "http://gateway:8080").rstrip("/")
OGC = f"{GATEWAY}/ogc"
TILES = f"{GATEWAY}/tiles"

TOWNS = "synthetic-towns"
RASTERS = {"synthetic-worldpop-2001": 1.0, "synthetic-worldpop-2011": 2.0}
RASTER_BBOX = [78.000, 17.936, 78.064, 18.000]
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
EPSG4326 = "http://www.opengis.net/def/crs/EPSG/0/4326"
REL = "http://www.opengis.net/def/rel/ogc/1.0/"
PUBLISH_TIMEOUT = 45


@pytest.fixture(scope="session")
def http():
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="session")
def db():
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "db"), port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "cgis"), user=os.environ.get("POSTGRES_USER", "cgis"),
        password=os.environ.get("POSTGRES_PASSWORD", ""))
    with psycopg.connect(conninfo, autocommit=True) as conn:
        yield conn


def link(doc, rel, type_=None):
    for item in doc.get("links", []):
        if item["rel"] == rel and (type_ is None or item.get("type") == type_):
            return item
    raise AssertionError(f"no link rel={rel} type={type_} in {[ (l['rel'], l.get('type')) for l in doc.get('links', [])]}")


def get_json(http, url, **kw):
    resp = http.get(url, **kw)
    assert resp.status_code == 200, (url, resp.status_code, resp.text[:300])
    return resp.json()


def png_image(resp):
    assert resp.status_code == 200, (resp.status_code, resp.text[:300])
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")
    return Image.open(io.BytesIO(resp.content)).convert("RGBA")


def tile_pixel(tms_id, lon, lat, zoom):
    """(tile, (col, row) pixel) of a lon/lat inside its tile at ``zoom``."""
    tms = morecantile.tms.get(tms_id)
    tile = tms.tile(lon, lat, zoom)
    bounds = tms.xy_bounds(tile)
    x, y = tms.xy(lon, lat)
    col = int((x - bounds.left) / (bounds.right - bounds.left) * 256)
    row = int((bounds.top - y) / (bounds.top - bounds.bottom) * 256)
    return tile, (col, row)


def insert_dataset(db, dataset_id, kind, asset_path=None, status="ready", version=1, provenance=None,
                   bbox=RASTER_BBOX, title=None):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, crs, bbox,
                   resolution_x, resolution_y, units, nodata, asset_path, source, provenance, status)
               VALUES (%s, %s, %s, %s, 'other', 'EPSG:4326', ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                   %s, %s, %s, %s, %s, 'integration-test', %s, %s)""",
            (dataset_id, version, title or f"TEST {dataset_id}", kind, *bbox,
             0.001 if kind == "raster" else None, 0.001 if kind == "raster" else None,
             "people_per_cell" if kind == "raster" else None, -9999 if kind == "raster" else None,
             asset_path, json.dumps(provenance or {"synthetic": True}), status))


def asset_path_of(db, dataset_id):
    """Stored asset_path of a ready dataset (layout is the ingestion service's choice)."""
    with db.cursor() as cur:
        cur.execute("SELECT asset_path FROM catalog.current_datasets WHERE dataset_id = %s", (dataset_id,))
        row = cur.fetchone()
    assert row and row[0], f"{dataset_id} has no asset_path in the catalog"
    return row[0]


def delete_dataset(db, dataset_id):
    with db.cursor() as cur:
        cur.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (dataset_id,))


@pytest.fixture
def temp_dataset(db):
    """Factory for catalog rows that are always removed afterwards."""
    created = []

    def make(kind, **kw):
        dataset_id = kw.pop("dataset_id", None) or f"itest-{kind}-{uuid.uuid4().hex[:8]}"
        insert_dataset(db, dataset_id, kind, **kw)
        created.append(dataset_id)
        return dataset_id

    yield make
    for dataset_id in created:
        delete_dataset(db, dataset_id)


def wait_for(predicate, timeout=PUBLISH_TIMEOUT, interval=1.0):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(interval)


def collection_ids(http):
    return {c["id"] for c in get_json(http, f"{OGC}/collections?f=json")["collections"]}
