# Clip + export: georeferencing, NoData and values verified by reopening the download with rasterio.

from __future__ import annotations

import io
import re

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile

import synthetic
from synthetic import CLIP, NODATA_CORNER, TOWN_A, TOWN_B, ZONAL, payload


# POST a clip and return the (successful) job. Used by every test here.
def clip(client, dataset_id, aoi, **extra):
    r = client.post(CLIP, json=payload(dataset_id, aoi, **extra))
    assert r.status_code == 200, r.text
    return r.json()


# GET the job's download link (gateway prefix stripped). Used by every test here.
def download(client, job):
    r = client.get(job["links"]["download"].removeprefix("/api"))
    assert r.status_code == 200, r.text
    return r


# Open a downloaded GeoTIFF in memory.
def open_download(response):
    return MemoryFile(io.BytesIO(response.content)).open()


# Non-masked values of band 1.
def valid_values(ds):
    return np.ma.compressed(ds.read(1, masked=True))


@pytest.mark.parametrize(
    "dataset_id, aoi, size, origin, total",
    [
        ("synthetic-worldpop-2001", TOWN_A, 10, (78.010, 17.990), 100),
        ("synthetic-worldpop-2011", TOWN_A, 10, (78.010, 17.990), 200),
        ("synthetic-worldpop-2001", TOWN_B, 5, (78.030, 17.955), 25),
        ("synthetic-worldpop-2011", TOWN_B, 5, (78.030, 17.955), 50),
    ],
    ids=["townA-2001", "townA-2011", "townB-2001", "townB-2011"],
)
# Native-grid clips have the exact size, origin, CRS, NoData, dtype, tags and totals.
def test_clip_geotiff_roundtrip(client, dataset_id, aoi, size, origin, total):
    job = clip(client, dataset_id, aoi, output_crs="EPSG:4326", format="GTiff")
    assert job["status"] == "completed"
    assert job["kind"] == "clip"
    result = job["result"]
    assert result["output"]["resampled"] is False
    assert result["output"]["sum_valid"] == pytest.approx(total)

    r = download(client, job)
    assert r.headers["content-type"].startswith("image/tiff")
    assert "application=geotiff" in r.headers["content-type"]
    disposition = r.headers["content-disposition"]
    assert re.fullmatch(r'attachment; filename="clip_[A-Za-z0-9._-]+_v1_[0-9a-f]{12}\.tif"', disposition)

    with open_download(r) as ds:
        assert ds.driver == "GTiff"
        assert (ds.width, ds.height) == (size, size)
        assert ds.crs.to_epsg() == 4326
        t = ds.transform
        assert t.a == pytest.approx(0.001) and t.e == pytest.approx(-0.001)
        assert t.b == 0 and t.d == 0
        assert (t.c, t.f) == pytest.approx(origin)
        assert ds.nodata == -9999
        assert ds.dtypes[0] == "float32"
        values = valid_values(ds)
        assert values.size == size * size
        assert values.sum() == pytest.approx(total)
        assert ds.tags()["DATASET_ID"] == dataset_id
        assert ds.tags()["UNITS"] == "people_per_cell"
        assert ds.units[0] == "people_per_cell"


# NoData pixels stay NoData and are counted as excluded.
def test_clip_preserves_nodata_and_excluded_counts(client):
    job = clip(client, "synthetic-worldpop-2001", NODATA_CORNER)
    assert job["result"]["count_valid"] == 36
    assert job["result"]["nodata_excluded"] == 64
    with open_download(download(client, job)) as ds:
        arr = ds.read(1)
        assert arr.shape == (10, 10)
        assert (arr[:4, :] == -9999).all() and (arr[:, :4] == -9999).all()
        assert (arr[4:, 4:] == 1).all()
        assert (ds.transform.c, ds.transform.f) == pytest.approx((78.0, 18.0))


# Pixels outside a triangle become NoData and the clip agrees with zonal statistics.
def test_polygon_clip_masks_outside_pixels_and_agrees_with_zonal(client):
    triangle = {"type": "Polygon", "coordinates": [[[78.010, 17.980], [78.030, 17.980], [78.010, 18.000 - 0.004], [78.010, 17.980]]]}
    job = clip(client, "synthetic-worldpop-2011", triangle)
    zonal = client.post(ZONAL, json=payload("synthetic-worldpop-2011", triangle)).json()
    with open_download(download(client, job)) as ds:
        values = valid_values(ds)
        arr = ds.read(1)
    assert values.size == zonal["count_valid"] == job["result"]["count_valid"]
    assert values.sum() == pytest.approx(zonal["sum"])
    assert (arr == -9999).any()  # pixels outside the triangle (bounding box corners) are NoData
    assert set(np.unique(values)) == {2.0}  # native values untouched


# format=cog writes a DEFLATE COG with the COG media type.
def test_cog_export(client):
    job = clip(client, "synthetic-worldpop-2001", TOWN_A, format="cog")
    assert job["input"]["format"] == "COG"
    assert job["result"]["output"]["driver_layout"] == "COG"
    r = download(client, job)
    assert "profile=cloud-optimized" in r.headers["content-type"]
    with open_download(r) as ds:
        assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert ds.tags(ns="IMAGE_STRUCTURE")["COMPRESSION"] == "DEFLATE"
        assert (ds.width, ds.height) == (10, 10)
        assert ds.nodata == -9999
        assert valid_values(ds).sum() == pytest.approx(100)


# A different output_crs reprojects (nearest) and warns that totals are not preserved.
def test_reprojected_output_is_flagged(client):
    job = clip(client, "synthetic-worldpop-2001", TOWN_A, output_crs="EPSG:3857")
    out = job["result"]["output"]
    assert out["resampled"] is True and out["resampling"] == "nearest"
    assert out["crs"] == "EPSG:3857"
    assert any("not preserved" in w.lower() for w in job["result"]["warnings"])
    with open_download(download(client, job)) as ds:
        assert ds.crs.to_epsg() == 3857
        assert ds.nodata == -9999
        assert set(np.unique(valid_values(ds))) == {1.0}


# A projected raster keeps its native CRS, pixel size and dtype; values match the brute-force reference.
def test_projected_raster_clip_keeps_native_crs(client, asset_root):
    expected_count, expected_sum = synthetic.brute_force_sum(asset_root / "utm_index.tif", TOWN_A, nodata=-1)
    job = clip(client, "utm-index", TOWN_A)
    with open_download(download(client, job)) as ds:
        assert ds.crs.to_epsg() == 32644
        assert ds.transform.a == 100 and ds.transform.e == -100
        assert ds.dtypes[0] == "int32"
        values = valid_values(ds)
    assert values.size == expected_count
    assert int(values.sum()) == expected_sum


# A raster without NoData is exported with an internal mask band.
def test_raster_without_nodata_exports_mask_band(client):
    triangle = {"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.01, 17.99], [78.01, 17.98]]]}
    job = clip(client, "unmasked-int", triangle)
    assert job["result"]["output"]["mask"] == "internal_mask_band"
    with open_download(download(client, job)) as ds:
        assert ds.nodata is None
        values = valid_values(ds)
        assert 0 < values.size < 100
        assert values.size == job["result"]["count_valid"]
        assert values.sum() == 3 * values.size


# The metadata sidecar has job, provenance, grid and software info and no server paths.
def test_metadata_sidecar(client):
    job = clip(client, "synthetic-worldpop-2001", TOWN_A)
    r = client.get(job["links"]["metadata"].removeprefix("/api"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    meta = r.json()
    assert meta["job_id"] == job["job_id"]
    assert meta["dataset"]["provenance"]["synthetic"] is True
    assert meta["pixel_selection"]["all_touched"] is False
    assert meta["output"]["crs"] == "EPSG:4326"
    assert meta["output"]["transform_gdal"] == pytest.approx([78.01, 0.001, 0, 17.99, 0, -0.001])
    assert meta["software"]["gdal"] == rasterio.__gdal_version__
    assert "asset_path" not in r.text and "/data/assets" not in r.text
