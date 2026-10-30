"""Fixture generator conforms to contracts/SYNTHETIC_DATA.md and is deterministic."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
import rasterio
from rasterio.features import geometry_mask
from shapely.geometry import shape

from ingestion import fixtures

NODATA = -9999.0


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_generator_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    fixtures.generate(a)
    fixtures.generate(b)
    fixtures.generate(b)  # re-running in place is idempotent
    for name in ("synthetic_towns.geojson", "synthetic_test_aois.geojson", "expected_values.json", "manifest.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    for name in ("synthetic_2001.tif", "synthetic_2011.tif"):
        with rasterio.open(a / name) as x, rasterio.open(b / name) as y:
            assert x.profile == y.profile
            assert np.array_equal(x.read(), y.read())
            assert x.tags() == y.tags()
        # Same GDAL build -> byte-identical; tests above are the portable guarantee.
        assert _sha(a / name) == _sha(b / name)


@pytest.mark.parametrize("name,value", [("synthetic_2001.tif", 1.0), ("synthetic_2011.tif", 2.0)])
def test_raster_grid_and_nodata_layout(fixture_dir, name, value):
    with rasterio.open(fixture_dir / name) as src:
        assert (src.width, src.height, src.count) == (64, 64, 1)
        assert src.crs.to_epsg() == 4326
        t = src.transform
        assert (t.a, t.b, t.c, t.d, t.e, t.f) == pytest.approx((0.001, 0, 78.0, 0, -0.001, 18.0), abs=1e-12)
        assert tuple(src.bounds) == pytest.approx((78.0, 17.936, 78.064, 18.0), abs=1e-9)
        assert src.nodata == NODATA
        assert src.units == ("people_per_cell",)
        assert src.tags()["SYNTHETIC"] == "TRUE"
        arr = src.read(1)
    assert (arr[:4, :] == NODATA).all() and (arr[:, :4] == NODATA).all()
    assert (arr[4:, 4:] == value).all()
    assert int((arr != NODATA).sum()) == 60 * 60
    assert value != NODATA


def _zonal(fixture_dir, raster, geom):
    with rasterio.open(fixture_dir / raster) as src:
        inside = geometry_mask([geom], out_shape=(src.height, src.width), transform=src.transform,
                               all_touched=False, invert=True)
        arr = src.read(1)
    valid = inside & (arr != NODATA)
    return int(inside.sum()), int(valid.sum()), float(arr[valid].sum())


@pytest.mark.parametrize("unit_id,count,s2001,s2011", [("town-a", 100, 100, 200), ("town-b", 25, 25, 50)])
def test_towns_produce_known_sums(fixture_dir, unit_id, count, s2001, s2011):
    fc = json.loads((fixture_dir / "synthetic_towns.geojson").read_text())
    feat = next(f for f in fc["features"] if f["properties"]["unit_id"] == unit_id)
    geom = shape(feat["geometry"])
    assert _zonal(fixture_dir, "synthetic_2001.tif", geom) == (count, count, s2001)
    assert _zonal(fixture_dir, "synthetic_2011.tif", geom) == (count, count, s2011)
    exp = json.loads((fixture_dir / "expected_values.json").read_text())["aois"][unit_id]
    assert exp["valid_pixels"] == count and exp["sum"] == {"2001": s2001, "2011": s2011}


def test_test_aois_match_expected(fixture_dir):
    fc = json.loads((fixture_dir / "synthetic_test_aois.geojson").read_text())
    by_id = {f["properties"]["aoi_id"]: f for f in fc["features"]}
    total, valid, s = _zonal(fixture_dir, "synthetic_2011.tif", shape(by_id["aoi-nodata-overlap"]["geometry"]))
    assert (total, valid, s) == (100, 36, 72)
    assert by_id["aoi-nodata-overlap"]["properties"]["expected"]["nodata_pixels"] == 64
    total, valid, _ = _zonal(fixture_dir, "synthetic_2001.tif", shape(by_id["aoi-disjoint"]["geometry"]))
    assert total == 0 and by_id["aoi-disjoint"]["properties"]["expected"]["expect"] == "no_overlap_error"


def test_towns_geojson_properties(fixture_dir):
    fc = json.loads((fixture_dir / "synthetic_towns.geojson").read_text())
    props = {f["properties"]["unit_id"]: f["properties"] for f in fc["features"]}
    assert props["town-a"]["name"] == "Town A" and props["town-b"]["name"] == "Town B"
    for p in props.values():
        assert p["level"] == "synthetic_town" and p["source"] == "deterministic-fixture"
    assert shape(fc["features"][0]["geometry"]).bounds == pytest.approx((78.010, 17.980, 78.020, 17.990))
    assert shape(fc["features"][1]["geometry"]).bounds == pytest.approx((78.030, 17.950, 78.035, 17.955))


def test_manifest_is_conspicuously_synthetic(fixture_dir):
    m = json.loads((fixture_dir / "manifest.json").read_text())
    assert m["synthetic"] is True
    ids = {d["dataset_id"]: d for d in m["datasets"]}
    assert set(ids) == {"synthetic-worldpop-2001", "synthetic-worldpop-2011", "synthetic-towns"}
    for d in ids.values():
        assert "SYNTHETIC" in d["title"] and d["source"] == "deterministic-fixture"
        assert d["provenance"]["synthetic"] is True and d["version"] == 1
        assert not d["file"].startswith("/")  # relative to the manifest directory
    assert ids["synthetic-worldpop-2001"]["year"] == 2001 and ids["synthetic-worldpop-2011"]["year"] == 2011
    assert ids["synthetic-worldpop-2001"]["units"] == "people_per_cell"
