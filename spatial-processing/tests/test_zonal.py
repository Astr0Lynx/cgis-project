"""Zonal statistics correctness on real synthetic GeoTIFFs (TEST_PLAN.md values)."""

from __future__ import annotations

import pytest
from pyproj import Transformer

import synthetic
from synthetic import DISJOINT, NODATA_CORNER, TOWN_A, TOWN_B, rect


def zonal(client, dataset_id, aoi, aoi_crs="EPSG:4326", **extra):
    return client.post(
        "/operations/zonal-statistics",
        json={"dataset_id": dataset_id, "aoi": aoi, "aoi_crs": aoi_crs, **extra},
    )


@pytest.mark.parametrize(
    "dataset_id, aoi, count, total, mean",
    [
        ("synthetic-worldpop-2001", TOWN_A, 100, 100, 1),
        ("synthetic-worldpop-2011", TOWN_A, 100, 200, 2),
        ("synthetic-worldpop-2001", TOWN_B, 25, 25, 1),
        ("synthetic-worldpop-2011", TOWN_B, 25, 50, 2),
    ],
    ids=["townA-2001", "townA-2011", "townB-2001", "townB-2011"],
)
def test_fixture_expected_values(client, dataset_id, aoi, count, total, mean):
    r = zonal(client, dataset_id, aoi)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count_valid"] == count
    assert body["sum"] == pytest.approx(total)
    assert body["mean"] == pytest.approx(mean)
    assert body["nodata_excluded"] == 0
    assert body["pixels_in_aoi"] == count
    assert body["units"] == "people_per_cell"
    assert body["sum_semantics"] == "total_count"
    assert body["aoi_crs"] == "EPSG:4326"
    assert body["raster_crs"] == "EPSG:4326"
    assert body["pixel_selection"]["rule"] == "pixel_center"
    assert body["pixel_selection"]["all_touched"] is False
    assert body["nodata_value"] == -9999
    assert body["dataset"]["provenance"]["synthetic"] is True
    assert body["status"] == "completed"


def test_aoi_overlapping_nodata_counts_only_valid_pixels(client):
    body = zonal(client, "synthetic-worldpop-2001", NODATA_CORNER).json()
    # 10x10 block, first 4 rows + first 4 cols NoData -> 6x6 valid
    assert body["pixels_in_aoi"] == 100
    assert body["count_valid"] == 36
    assert body["nodata_excluded"] == 64
    assert body["sum"] == pytest.approx(36)
    assert body["mean"] == pytest.approx(1)


def test_all_nodata_aoi_reports_null_not_zero(client):
    body = zonal(client, "synthetic-worldpop-2011", rect(78.000, 17.996, 78.004, 18.000)).json()
    assert body["pixels_in_aoi"] == 16
    assert body["count_valid"] == 0
    assert body["nodata_excluded"] == 16
    assert body["sum"] is None and body["mean"] is None
    assert any("NoData" in w for w in body["warnings"])


def test_partial_overlap_with_raster_edge(client):
    # Raster ends at lon 78.064 / lat 17.936: AOI extends past the SE corner -> 4x4 pixels.
    body = zonal(client, "synthetic-worldpop-2001", rect(78.060, 17.930, 78.070, 17.940)).json()
    assert body["count_valid"] == 16
    assert body["sum"] == pytest.approx(16)


def test_projected_aoi_crs_is_transformed_to_raster_crs(client):
    to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    ring = [list(to_merc.transform(x, y)) for x, y in TOWN_A["coordinates"][0]]
    body = zonal(client, "synthetic-worldpop-2001", {"type": "Polygon", "coordinates": [ring]}, "EPSG:3857").json()
    assert body["aoi_crs"] == "EPSG:3857"
    assert body["count_valid"] == 100
    assert body["sum"] == pytest.approx(100)


def test_crs84_alias_and_feature_wrapper(client):
    feature = {"type": "Feature", "properties": {"name": "Town A"}, "geometry": TOWN_A}
    body = zonal(client, "synthetic-worldpop-2011", feature, "OGC:CRS84").json()
    assert body["sum"] == pytest.approx(200)


def test_multipolygon_sums_both_parts(client):
    multi = {"type": "MultiPolygon", "coordinates": [TOWN_A["coordinates"], TOWN_B["coordinates"]]}
    body = zonal(client, "synthetic-worldpop-2001", multi).json()
    assert body["count_valid"] == 125
    assert body["sum"] == pytest.approx(125)


def test_operation_subset(client):
    body = zonal(client, "synthetic-worldpop-2001", TOWN_A, operations=["sum"]).json()
    assert body["operations"] == ["sum"]
    assert body["sum"] == pytest.approx(100)
    assert body["mean"] is None and body["count_valid"] is None
    # NoData accounting is always reported
    assert body["nodata_excluded"] == 0


def test_different_resolution_raster(client):
    # 0.0005 deg pixels with 0.25 people/cell: Town A = 20x20 = 400 pixels, same total.
    body = zonal(client, "fine-2001", TOWN_A).json()
    assert body["count_valid"] == 400
    assert body["sum"] == pytest.approx(100)


@pytest.mark.parametrize("aoi", [TOWN_A, TOWN_B, NODATA_CORNER], ids=["townA", "townB", "corner"])
def test_projected_raster_matches_independent_brute_force(client, asset_root, aoi):
    """Raster in EPSG:32644 (100 m), AOI in EPSG:4326: compare with per-pixel-center reference."""
    expected_count, expected_sum = synthetic.brute_force_sum(asset_root / "utm_index.tif", aoi, nodata=-1)
    body = zonal(client, "utm-index", aoi).json()
    assert body["raster_crs"] == "EPSG:32644"
    assert body["count_valid"] == expected_count
    assert body["sum"] == expected_sum
    assert isinstance(body["sum"], int)  # integer raster -> integer sum


def test_integer_raster_without_nodata(client):
    body = zonal(client, "unmasked-int", TOWN_A).json()
    assert body["count_valid"] == 100
    assert body["sum"] == 300 and isinstance(body["sum"], int)
    assert body["nodata_value"] is None


def test_current_version_is_highest_ready(client):
    body = zonal(client, "versioned", TOWN_A).json()
    assert body["dataset_version"] == 2
    assert body["sum"] == pytest.approx(100)  # v2 = value 1 (v1 would be 200)


def test_absolute_asset_path_inside_root_is_tolerated(client):
    assert zonal(client, "absolute-inside", TOWN_A).json()["sum"] == pytest.approx(100)


def test_disjoint_aoi_rejected(client):
    r = zonal(client, "synthetic-worldpop-2001", DISJOINT)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AOI_OUTSIDE_RASTER"
