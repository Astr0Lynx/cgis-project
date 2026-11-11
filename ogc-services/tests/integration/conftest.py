# Integration fixtures and helpers: every request goes over HTTP through the gateway (CGIS_GATEWAY_URL);
# temporary catalog rows are inserted directly into the database. Run by tests/run.sh modules.

import hashlib
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


# Session-wide HTTP client (redirects not followed).
@pytest.fixture(scope="session")
def http():
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        yield client


# Session-wide autocommit connection to the stack database.
@pytest.fixture(scope="session")
def db():
    conninfo = psycopg.conninfo.make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "db"), port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "cgis"), user=os.environ.get("POSTGRES_USER", "cgis"),
        password=os.environ.get("POSTGRES_PASSWORD", ""))
    with psycopg.connect(conninfo, autocommit=True) as conn:
        yield conn


# First link with the given rel (and type); fails the test if missing.
def link(doc, rel, type_=None):
    for item in doc.get("links", []):
        if item["rel"] == rel and (type_ is None or item.get("type") == type_):
            return item
    raise AssertionError(f"no link rel={rel} type={type_} in {[ (l['rel'], l.get('type')) for l in doc.get('links', [])]}")


# GET a URL, assert HTTP 200 and return the JSON body.
def get_json(http, url, **kw):
    resp = http.get(url, **kw)
    assert resp.status_code == 200, (url, resp.status_code, resp.text[:300])
    return resp.json()


# Asserts a 200 PNG response and returns it as an RGBA image.
def png_image(resp):
    assert resp.status_code == 200, (resp.status_code, resp.text[:300])
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")
    return Image.open(io.BytesIO(resp.content)).convert("RGBA")


# (tile, (col, row) pixel) of a lon/lat inside its tile at the given zoom.
def tile_pixel(tms_id, lon, lat, zoom):
    tms = morecantile.tms.get(tms_id)
    tile = tms.tile(lon, lat, zoom)
    bounds = tms.xy_bounds(tile)
    x, y = tms.xy(lon, lat)
    col = int((x - bounds.left) / (bounds.right - bounds.left) * 256)
    row = int((bounds.top - y) / (bounds.top - bounds.bottom) * 256)
    return tile, (col, row)


# Inserts a catalog row (with a dummy checksum, required for ready rows by the schema).
def insert_dataset(db, dataset_id, kind, asset_path=None, status="ready", version=1, provenance=None,
                   bbox=RASTER_BBOX, title=None):
    checksum = hashlib.sha256(f"{dataset_id}:{version}".encode()).hexdigest()
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, crs, bbox,
                   resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance, status)
               VALUES (%s, %s, %s, %s, 'other', 'EPSG:4326', ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                   %s, %s, %s, %s, %s, %s, 'integration-test', %s, %s)""",
            (dataset_id, version, title or f"TEST {dataset_id}", kind, *bbox,
             0.001 if kind == "raster" else None, 0.001 if kind == "raster" else None,
             "people_per_cell" if kind == "raster" else None, -9999 if kind == "raster" else None,
             asset_path, checksum, json.dumps(provenance or {"synthetic": True}), status))


# Stored asset_path of a ready dataset (layout is the ingestion service's choice).
def asset_path_of(db, dataset_id):
    with db.cursor() as cur:
        cur.execute("SELECT asset_path FROM catalog.current_datasets WHERE dataset_id = %s", (dataset_id,))
        row = cur.fetchone()
    assert row and row[0], f"{dataset_id} has no asset_path in the catalog"
    return row[0]


# Deletes every version of a catalog dataset.
def delete_dataset(db, dataset_id):
    with db.cursor() as cur:
        cur.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (dataset_id,))


# Factory for itest-* catalog rows that are always removed afterwards.
@pytest.fixture
def temp_dataset(db):
    created = []

    # Inserts one temporary row and returns its dataset_id.
    def make(kind, **kw):
        dataset_id = kw.pop("dataset_id", None) or f"itest-{kind}-{uuid.uuid4().hex[:8]}"
        insert_dataset(db, dataset_id, kind, **kw)
        created.append(dataset_id)
        return dataset_id

    yield make
    for dataset_id in created:
        delete_dataset(db, dataset_id)


# Polls predicate until truthy or timeout (publication refresh is periodic); returns its last result.
def wait_for(predicate, timeout=PUBLISH_TIMEOUT, interval=1.0):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(interval)


# IDs of the collections currently published at /ogc/collections.
def collection_ids(http):
    return {c["id"] for c in get_json(http, f"{OGC}/collections?f=json")["collections"]}
