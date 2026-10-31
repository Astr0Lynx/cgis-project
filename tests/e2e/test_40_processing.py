"""Zonal statistics, clip and GeoTIFF export through the gateway (TEST_PLAN 6-7; P0 8-9)."""

from __future__ import annotations

import hashlib
import json
import math
import re

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile

from conftest import (API, EXPECTED, FIXTURES, OGC, RASTER_2001, RASTER_2011, RASTERS, TOWN_A, TOWN_GEOMS,
                      TOWNS, error_code, post_json)


def zonal(http, dataset, aoi, aoi_crs="EPSG:4326", **extra):
    return post_json(http, "/operations/zonal-statistics",
                     {"dataset_id": dataset, "aoi": aoi, "aoi_crs": aoi_crs, **extra})


def to_3857(lon, lat):
    r = 6378137.0
    return r * math.radians(lon), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


@pytest.mark.parametrize("town,year", sorted(EXPECTED))
def test_zonal_known_values(http, town, year):
    count, total, mean = EXPECTED[(town, year)]
    resp = zonal(http, RASTERS[year], TOWN_GEOMS[town], operations=["sum", "mean", "count"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dataset_id"] == RASTERS[year]
    assert body["count_valid"] == count
    assert body["sum"] == total
    assert body["mean"] == mean
    assert body["units"] == "people_per_cell"
    assert body["sum_semantics"] == "total_count"
    assert body["nodata_excluded"] == 0
    assert body["aoi_crs"] == "EPSG:4326"
    assert body["pixel_selection"]["rule"] == "pixel_center"
    assert body["pixel_selection"]["all_touched"] is False
    assert body["status"] == "completed" and body["job_id"]


@pytest.mark.parametrize("year,total", [(2001, 100), (2011, 200)])
def test_zonal_with_geometry_served_by_ogc_features(http, year, total):
    """OGC Features -> processing: use the boundary exactly as a client receives it."""
    feature = http.get(f"{OGC}/collections/{TOWNS}/items/town-a", params={"f": "json"}).json()
    resp = zonal(http, RASTERS[year], feature["geometry"])
    assert resp.status_code == 200 and resp.json()["sum"] == total


def test_zonal_aoi_in_projected_crs_is_transformed(http):
    (w, s), (e, n) = to_3857(78.010, 17.980), to_3857(78.020, 17.990)
    aoi = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    body = zonal(http, RASTER_2001, aoi, aoi_crs="EPSG:3857").json()
    assert body["count_valid"] == 100 and body["sum"] == 100 and body["aoi_crs"] == "EPSG:3857"


def test_zonal_excludes_nodata(http):
    aois = json.loads((FIXTURES / "synthetic_test_aois.geojson").read_text())
    feature = next(f for f in aois["features"] if f["properties"].get("aoi_id", f.get("id")) == "aoi-nodata-overlap"
                   or f.get("id") == "aoi-nodata-overlap")
    for year, factor in ((2001, 1), (2011, 2)):
        body = zonal(http, RASTERS[year], feature["geometry"]).json()
        assert body["pixels_in_aoi"] == 100
        assert body["count_valid"] == 36
        assert body["nodata_excluded"] == 64
        assert body["sum"] == 36 * factor


def test_zonal_disjoint_aoi_is_an_explicit_error(http):
    aoi = {"type": "Polygon", "coordinates": [[[79.0, 19.0], [79.01, 19.0], [79.01, 19.01], [79.0, 19.01], [79.0, 19.0]]]}
    resp = zonal(http, RASTER_2001, aoi)
    assert resp.status_code == 422 and error_code(resp) == "AOI_OUTSIDE_RASTER"


def test_clip_town_a_exports_georeferenced_geotiff(http, state_dir):
    resp = post_json(http, "/operations/clip",
                     {"dataset_id": RASTER_2001, "aoi": TOWN_A, "aoi_crs": "EPSG:4326",
                      "output_crs": "EPSG:4326", "format": "GTiff"})
    assert resp.status_code in (200, 201), resp.text
    job = resp.json()
    assert job["status"] == "completed" and job["kind"] == "clip"

    status = http.get(f"{API}/operations/{job['job_id']}")
    assert status.status_code == 200 and status.json()["status"] == "completed"

    dl = http.get(f"{API}/downloads/{job['job_id']}")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("image/tiff")
    disp = dl.headers["content-disposition"]
    assert re.fullmatch(r'attachment; filename="clip_synthetic-worldpop-2001_v1_[0-9a-f]{12}\.tif"', disp), disp

    with MemoryFile(dl.content) as mem, mem.open() as src:
        assert src.driver == "GTiff" and src.count == 1
        assert src.crs.to_epsg() == 4326
        assert (src.width, src.height) == (10, 10)
        t = src.transform
        assert (t.a, t.e) == pytest.approx((0.001, -0.001))
        assert (t.c, t.f) == pytest.approx((78.010, 17.990))
        assert src.bounds == pytest.approx((78.010, 17.980, 78.020, 17.990))
        assert src.nodata == -9999
        data = src.read(1)
        assert np.all(data == 1) and data.sum() == 100
    (state_dir / "clip_town_a_2001.tif").write_bytes(dl.content)
    (state_dir / "clip_job.json").write_text(json.dumps(
        {"job_id": job["job_id"], "sha256": hashlib.sha256(dl.content).hexdigest()}))


def test_clip_town_b_2011_as_cog(http):
    job = post_json(http, "/operations/clip",
                    {"dataset_id": RASTER_2011, "aoi": TOWN_GEOMS["town-b"], "aoi_crs": "EPSG:4326",
                     "format": "COG"}).json()
    assert job["status"] == "completed"
    dl = http.get(f"{API}/downloads/{job['job_id']}")
    assert "cloud-optimized" in dl.headers["content-type"]
    with MemoryFile(dl.content) as mem, mem.open() as src:
        assert (src.width, src.height) == (5, 5)
        assert src.read(1).sum() == 50
        assert src.tags().get("UNITS") == "people_per_cell" or src.units[0] == "people_per_cell"
    meta = http.get(f"{API}/downloads/{job['job_id']}/metadata")
    assert meta.status_code == 200


def test_clip_overlapping_nodata_writes_nodata_not_zero(http):
    aoi = {"type": "Polygon", "coordinates": [[[78.000, 17.990], [78.010, 17.990], [78.010, 18.000],
                                                [78.000, 18.000], [78.000, 17.990]]]}
    job = post_json(http, "/operations/clip", {"dataset_id": RASTER_2001, "aoi": aoi, "aoi_crs": "EPSG:4326"}).json()
    dl = http.get(f"{API}/downloads/{job['job_id']}")
    with MemoryFile(dl.content) as mem, mem.open() as src:
        data = src.read(1)
        assert int((data == -9999).sum()) == 64 and int((data == 1).sum()) == 36 and not (data == 0).any()


def test_zonal_job_has_status_but_no_download(http):
    job = zonal(http, RASTER_2001, TOWN_A).json()
    status = http.get(f"{API}/operations/{job['job_id']}")
    assert status.status_code == 200 and status.json()["status"] == "completed"
    dl = http.get(f"{API}/downloads/{job['job_id']}")
    assert dl.status_code == 409 and error_code(dl) == "NO_DOWNLOAD"


def test_processing_openapi_is_published(http):
    doc = http.get(f"{API}/openapi.json").json()
    assert {"/operations/clip", "/operations/zonal-statistics"} <= set(doc["paths"])
    assert http.get(f"{API}/docs").status_code == 200
