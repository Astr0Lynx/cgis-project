# Tests for validate.py integrity checks.
import numpy as np
import pytest

from cgis_data import validate as V


# Missing, duplicate and unexpected years are reported.
def test_year_coverage_ok_and_failures():
    V.check_year_coverage(range(2001, 2012))
    with pytest.raises(V.ValidationIssue, match="missing=\\[2007\\]"):
        V.check_year_coverage([y for y in range(2001, 2012) if y != 2007])
    with pytest.raises(V.ValidationIssue, match="duplicate=\\[2003\\]"):
        V.check_year_coverage(list(range(2001, 2012)) + [2003])
    with pytest.raises(V.ValidationIssue, match="unexpected"):
        V.check_year_coverage(list(range(2001, 2012)) + [2012])


# Missing NRES epoch is rejected.
def test_ghsl_epochs():
    V.check_ghsl_epochs([2000, 2005, 2010], [2000, 2005, 2010])
    with pytest.raises(V.ValidationIssue):
        V.check_ghsl_epochs([2000, 2005, 2010], [2000, 2010])  # NRES missing 2005


# Density/volume unit labels are rejected.
def test_density_label_rejected(make_raster):
    p = make_raster(data=np.ones((8, 8)))
    for bad in ("people_per_km2", "population_density", "m3_per_cell"):
        with pytest.raises(V.ValidationIssue):
            V.check_units(bad, p)


# Count rasters pass; negatives are rejected.
def test_count_raster_ok_and_negative_rejected(make_raster):
    assert V.check_units("people_per_cell", make_raster(data=np.full((8, 8), 3.0)))["max"] == 3.0
    d = np.full((8, 8), 3.0); d[0, 0] = -2
    with pytest.raises(V.ValidationIssue, match="negative"):
        V.check_units("people_per_cell", make_raster("neg.tif", data=d))


# m2_per_cell values above the cell area are rejected.
def test_m2_per_cell_bounded_by_cell_area(make_raster):
    ok = make_raster("ok.tif", data=np.full((8, 8), 9000.0))
    assert V.check_units("m2_per_cell", ok, cell_area_m2=10000.0)["max"] == 9000.0
    bad = make_raster("bad.tif", data=np.full((8, 8), 25000.0))      # e.g. volume or wrong unit
    with pytest.raises(V.ValidationIssue, match="exceeds cell area"):
        V.check_units("m2_per_cell", bad, cell_area_m2=10000.0)
    with pytest.raises(V.ValidationIssue, match="cell_area_m2 required"):
        V.check_units("m2_per_cell", ok)


# NoData is ignored; an all-NoData raster is rejected.
def test_nodata_ignored_in_unit_check_and_all_nodata_rejected(make_raster):
    d = np.full((8, 8), -99999.0); d[0, 0] = 5
    assert V.check_units("people_per_cell", make_raster("a.tif", data=d))["valid_cells"] == 1
    with pytest.raises(V.ValidationIssue, match="no valid"):
        V.check_units("people_per_cell", make_raster("b.tif", data=np.full((8, 8), -99999.0)))


# Duplicate and blank census codes are rejected.
def test_duplicate_and_blank_census_codes():
    V.check_unique_census_codes(["a", "b"])
    with pytest.raises(V.ValidationIssue, match="duplicate"):
        V.check_unique_census_codes(["a", "b", "a"])
    with pytest.raises(V.ValidationIssue, match="blank"):
        V.check_unique_census_codes(["a", " "])


# Join report lists matched and one-sided codes; duplicates raise.
def test_census_join_report():
    r = V.check_census_join(["1", "2", "3"], ["2", "3", "4"])
    assert r == {"matched": 2, "census_only": ["1"], "polygon_only": ["4"]}
    with pytest.raises(V.ValidationIssue):
        V.check_census_join(["1", "1"], ["1"])


# Square polygon feature helper.
def _sq(x, y, s=1.0):
    return {"geometry": {"type": "Polygon", "coordinates": [[(x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y)]]}}


# Overlapping and invalid polygons are rejected; touching ones pass.
def test_polygon_overlap_and_invalid_detected():
    assert V.check_polygons([_sq(0, 0), _sq(1, 0)])["overlaps"] == 0           # touching is fine
    with pytest.raises(V.ValidationIssue, match="overlapping"):
        V.check_polygons([_sq(0, 0), _sq(0.5, 0)])
    bow = {"geometry": {"type": "Polygon", "coordinates": [[(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]]}}
    with pytest.raises(V.ValidationIssue, match="invalid"):
        V.check_polygons([bow])


# Uncovered footprint fraction is reported.
def test_polygon_footprint_holes_reported():
    fp = _sq(0, 0, 2)["geometry"]
    r = V.check_polygons([_sq(0, 0), _sq(1, 0), _sq(0, 1)], expected_footprint=fp)
    assert r["uncovered_fraction"] == pytest.approx(0.25)


# NoData cells inside a footprint are counted.
def test_raster_coverage_holes(make_raster):
    d = np.ones((8, 8), "float32"); d[2:4, 2:4] = -99999
    p = make_raster(data=d)                     # extent lon 77..77.08, lat 17.92..18
    fp = {"type": "Polygon", "coordinates": [[(77.0, 17.92), (77.08, 17.92), (77.08, 18.0), (77.0, 18.0), (77.0, 17.92)]]}
    r = V.coverage_holes(p, fp)
    assert r["cells_in_footprint"] == 64 and r["nodata_cells_in_footprint"] == 4


# An extra west column is a -1 col offset; off-lattice files raise.
def test_worldpop_grid_report_detects_extra_west_column(tmp_path):
    import rasterio
    from rasterio.transform import from_origin
    PIX = 0.00083333333
    # Writes a WorldPop-like raster of a given width/origin.
    def mk(name, w, x0):
        p = tmp_path / name
        with rasterio.open(p, "w", driver="GTiff", height=4, width=w, count=1, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(x0, 30.0, PIX, PIX), nodata=-99999.0) as ds:
            ds.write(np.ones((4, w), "float32"), 1)
        return str(p)
    rep = V.worldpop_grid_report({2011: mk("a.tif", 8, 68.0), 2010: mk("b.tif", 9, 68.0 - PIX)})
    assert rep["years"][2010]["offset_cols_rows_vs_ref"] == (-1, 0) and rep["identical_shape"] is False
    with pytest.raises(ValueError):                                  # off-lattice file is rejected
        V.worldpop_grid_report({2011: mk("c.tif", 8, 68.0), 2009: mk("d.tif", 8, 68.0 + 0.4 * PIX)})


# Admin layer count and duplicate-id checks.
def test_admin_layer_checks(tmp_path):
    import json
    sq = lambda x: {"type": "Polygon", "coordinates": [[(x, 0), (x + 1, 0), (x + 1, 1), (x, 1), (x, 0)]]}
    f = lambda i: {"type": "Feature", "properties": {"shapeID": f"id{i}"}, "geometry": sq(i)}
    p = tmp_path / "l.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [f(0), f(1)]}))
    assert V.check_admin_layer(str(p), 2)["ok"] and not V.check_admin_layer(str(p), 3)["ok"]
    p.write_text(json.dumps({"type": "FeatureCollection", "features": [f(0), f(0)]}))
    assert V.check_admin_layer(str(p), 2)["duplicate_ids"] == 1
