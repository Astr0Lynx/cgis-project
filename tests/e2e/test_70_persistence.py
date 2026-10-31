"""After `docker compose restart` (volumes kept): data and earlier results are still served (TEST_PLAN 9).

run_e2e.sh runs these with `-m persistence` after restarting every service.
"""

from __future__ import annotations

import hashlib
import json

import morecantile
import pytest

from conftest import API, OGC, RASTER_2001, RASTER_2011, TILES, TOWN_A, TOWNS, STATE, ogc_max_zoom, post_json

pytestmark = pytest.mark.persistence


def test_catalog_still_ready(db):
    n = db.execute("SELECT count(*) FROM catalog.current_datasets WHERE dataset_id = ANY(%s)",
                   ([RASTER_2001, RASTER_2011, TOWNS],)).fetchone()[0]
    assert n == 3


def test_features_still_served(http):
    fc = http.get(f"{OGC}/collections/{TOWNS}/items", params={"f": "json"}).json()
    assert {f["id"] for f in fc["features"]} == {"town-a", "town-b"}


def test_tiles_still_served(http):
    assert http.get(f"{TILES}/datasets/{RASTER_2011}/point/78.015,17.985").json()["values"] == [2.0]
    t = morecantile.tms.get("WebMercatorQuad").tile(78.015, 17.985, ogc_max_zoom(http, RASTER_2001))
    assert http.get(f"{OGC}/collections/{RASTER_2001}/tiles/WebMercatorQuad/{t.z}/{t.y}/{t.x}",
                    params={"f": "png"}).status_code == 200


def test_zonal_still_correct(http):
    body = post_json(http, "/operations/zonal-statistics",
                     {"dataset_id": RASTER_2001, "aoi": TOWN_A, "aoi_crs": "EPSG:4326"}).json()
    assert body["sum"] == 100 and body["count_valid"] == 100


def test_previous_export_still_downloadable(http):
    saved = json.loads((STATE / "clip_job.json").read_text())
    job = http.get(f"{API}/operations/{saved['job_id']}")
    assert job.status_code == 200 and job.json()["status"] == "completed"
    dl = http.get(f"{API}/downloads/{saved['job_id']}")
    assert dl.status_code == 200
    assert hashlib.sha256(dl.content).hexdigest() == saved["sha256"]
