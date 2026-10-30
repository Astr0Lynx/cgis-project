from __future__ import annotations

import json

import pytest
from pyproj import Transformer
from shapely.geometry import MultiPolygon

from ingestion.errors import ValidationError
from ingestion.manifest import parse_entry
from ingestion.vector import read_vector

from .conftest import spec


def _write_fc(path, features, crs=None):
    fc = {"type": "FeatureCollection", "features": features}
    if crs:
        fc["crs"] = {"type": "name", "properties": {"name": crs}}
    path.write_text(json.dumps(fc))
    return path


def _feat(uid, geom, **props):
    return {"type": "Feature", "properties": {"unit_id": uid, "name": f"Unit {uid}", **props}, "geometry": geom}


def _rect(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _vspec(path, **kw):
    raw = {"dataset_id": "test-units", "version": 1, "title": "Test units", "kind": "vector",
           "product_level": "source", "source": "unit-test", "file": str(path)}
    raw.update(kw)
    return parse_entry(raw, path.parent)


def test_fixture_towns_normalized_to_multipolygon(fixture_dir):
    info = read_vector(spec(fixture_dir, "synthetic-towns"))
    assert [f.unit_id for f in info.features] == ["town-a", "town-b"]
    assert all(isinstance(f.geom, MultiPolygon) for f in info.features)
    assert info.features[0].geom.bounds == pytest.approx((78.01, 17.98, 78.02, 17.99))
    assert info.features[0].level == "synthetic_town" and info.features[0].name == "Town A"
    assert info.features[0].properties["source"] == "deterministic-fixture"
    assert info.bbox_wgs84 == pytest.approx([78.01, 17.95, 78.035, 17.99])
    assert info.source_crs == "EPSG:4326"


def test_normalized_geojson_is_deterministic(fixture_dir):
    s = spec(fixture_dir, "synthetic-towns")
    assert read_vector(s).to_geojson_bytes(s) == read_vector(s).to_geojson_bytes(s)


def test_legacy_crs_member_is_reprojected(tmp_path):
    t = Transformer.from_crs(4326, 32644, always_xy=True)
    (w, s), (e, n) = t.transform(78.01, 17.98), t.transform(78.02, 17.99)
    p = _write_fc(tmp_path / "utm.geojson", [_feat("u1", _rect(w, s, e, n))], crs="urn:ogc:def:crs:EPSG::32644")
    info = read_vector(_vspec(p))
    assert info.source_crs == "EPSG:32644"
    minx, miny, maxx, maxy = info.features[0].geom.bounds
    assert minx == pytest.approx(78.01, abs=1e-3) and maxy == pytest.approx(17.99, abs=1e-3)


def test_crs84_and_prefix_and_custom_properties(tmp_path):
    feats = [{"type": "Feature", "properties": {"code": 7, "label": "Seven", "tier": 3},
              "geometry": _rect(1, 1, 2, 2)}]
    p = _write_fc(tmp_path / "x.geojson", feats, crs="urn:ogc:def:crs:OGC:1.3:CRS84")
    info = read_vector(_vspec(p, id_property="code", name_property="label", level_property="tier",
                              unit_id_prefix="in-dist-"))
    f = info.features[0]
    assert (f.unit_id, f.name, f.level) == ("in-dist-7", "Seven", "3")


@pytest.mark.parametrize("features,code", [
    ([_feat("bow", {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]})],
     "invalid_geometry"),
    ([_feat("pt", {"type": "Point", "coordinates": [0, 0]})], "unsupported_geometry"),
    ([_feat("line", {"type": "LineString", "coordinates": [[0, 0], [1, 1]]})], "unsupported_geometry"),
    ([_feat("none", None)], "unsupported_geometry"),
    ([_feat("empty", {"type": "Polygon", "coordinates": []})], "invalid_geometry"),
    ([_feat("a", _rect(0, 0, 1, 1)), _feat("a", _rect(2, 2, 3, 3))], "duplicate_unit_id"),
    ([{"type": "Feature", "properties": {"name": "x"}, "geometry": _rect(0, 0, 1, 1)}], "missing_unit_id"),
    ([{"type": "Feature", "properties": {"unit_id": "x"}, "geometry": _rect(0, 0, 1, 1)}], "missing_name"),
    ([_feat("far", _rect(500000, 2000000, 500100, 2000100))], "invalid_coordinates"),
    ([], "empty_vector"),
])
def test_invalid_vectors_rejected(tmp_path, features, code):
    p = _write_fc(tmp_path / "bad.geojson", features)
    with pytest.raises(ValidationError) as ei:
        read_vector(_vspec(p))
    assert ei.value.code == code


def test_unknown_crs_and_corrupt_json(tmp_path):
    p = _write_fc(tmp_path / "c.geojson", [_feat("a", _rect(0, 0, 1, 1))], crs="EPSG:99999999")
    with pytest.raises(ValidationError) as ei:
        read_vector(_vspec(p))
    assert ei.value.code == "unsupported_crs"
    bad = tmp_path / "broken.geojson"
    bad.write_text('{"type": "FeatureCollection", "features": [')
    with pytest.raises(ValidationError) as ei:
        read_vector(_vspec(bad))
    assert ei.value.code == "corrupt_vector"


def test_unsupported_vector_format(tmp_path):
    p = tmp_path / "units.gpkg"
    p.write_bytes(b"SQLite format 3\x00")
    with pytest.raises(ValidationError) as ei:
        read_vector(_vspec(p))
    assert ei.value.code == "unsupported_format"


def test_3d_coordinates_are_flattened(tmp_path):
    geom = {"type": "Polygon", "coordinates": [[[0, 0, 5], [1, 0, 5], [1, 1, 5], [0, 1, 5], [0, 0, 5]]]}
    info = read_vector(_vspec(_write_fc(tmp_path / "z.geojson", [_feat("z", geom)])))
    assert not info.features[0].geom.has_z
