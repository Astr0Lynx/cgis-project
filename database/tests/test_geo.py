"""geo.admin_units: synthetic town import, normalisation, validity, spatial selection, CRS handling."""

import json

import psycopg
import pytest

from cgis_db import fixtures, queries

TOWN_A = {"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.02, 17.99],
                                               [78.01, 17.99], [78.01, 17.98]]]}
EXPECTED_EXTENT = {"town-a": (78.010, 17.980, 78.020, 17.990), "town-b": (78.030, 17.950, 78.035, 17.955)}


def upsert(conn, unit_id, ewkt, repair=False, dataset=(None, None)):
    return conn.execute(
        "SELECT unit_id, GeometryType(geom), ST_SRID(geom) FROM geo.upsert_admin_unit("
        "%s, %s, 'test', ST_GeomFromEWKT(%s), '{}', %s, %s, 'test', '{}', %s)",
        (unit_id, unit_id.upper(), ewkt, dataset[0], dataset[1], repair)).fetchone()


def test_synthetic_towns_imported_as_valid_multipolygons(conn):
    rows = conn.execute(
        "SELECT unit_id, name, level, GeometryType(geom), ST_SRID(geom), ST_IsValid(geom),"
        " ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom), dataset_id, dataset_version"
        " FROM geo.admin_units ORDER BY unit_id").fetchall()
    assert [r[:6] for r in rows] == [
        ("town-a", "Town A", "synthetic_town", "MULTIPOLYGON", 4326, True),
        ("town-b", "Town B", "synthetic_town", "MULTIPOLYGON", 4326, True),
    ]
    for r in rows:
        assert r[6:10] == pytest.approx(EXPECTED_EXTENT[r[0]])
        assert r[10:] == ("synthetic-towns", 1)


def test_towns_catalog_row_is_ready_and_synthetic(conn):
    d = queries.resolve_dataset(conn, "synthetic-towns")
    assert d["kind"] == "vector" and d["status"] == "ready" and d["crs"] == "EPSG:4326"
    assert d["provenance"]["synthetic"] is True and "SYNTHETIC" in d["title"]
    xs = [p[0] for p in d["bbox"]["coordinates"][0]]
    ys = [p[1] for p in d["bbox"]["coordinates"][0]]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((78.01, 17.95, 78.035, 17.99))


def test_fixture_load_is_idempotent(conn):
    assert fixtures.load_units(conn)["outcome"] == "unchanged"
    assert conn.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0] == 2


def test_fixture_conflicting_file_rejected(conn, tmp_path):
    doc = json.loads(fixtures.STUB_GEOJSON.read_text())
    doc["features"][0]["geometry"]["coordinates"][0][1][0] = 78.021
    p = tmp_path / "changed.geojson"
    p.write_text(json.dumps(doc))
    with pytest.raises(queries.CatalogConflict):
        fixtures.load_units(conn, p)


@pytest.mark.parametrize("doc, msg", [
    ({"type": "Feature"}, "FeatureCollection"),
    ({"type": "FeatureCollection", "features": []}, "no features"),
    ({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "x"},
                                                 "geometry": TOWN_A}]}, "unit_id"),
    ({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
      "features": [{"type": "Feature", "properties": {"unit_id": "a", "name": "A"}, "geometry": TOWN_A}]},
     "crs"),
])
def test_fixture_rejects_malformed_geojson(conn, tmp_path, doc, msg):
    p = tmp_path / "bad.geojson"
    p.write_text(json.dumps(doc))
    with pytest.raises(fixtures.FixtureError, match=msg):
        fixtures.load_units(conn, p, dataset_id="bad-fixture")


def test_polygon_normalised_to_multipolygon(conn):
    assert upsert(conn, "p1", "SRID=4326;POLYGON((1 1,2 1,2 2,1 2,1 1))") == ("p1", "MULTIPOLYGON", 4326)


def test_3d_input_forced_2d(conn):
    upsert(conn, "p3d", "SRID=4326;POLYGON Z((1 1 5,2 1 5,2 2 5,1 2 5,1 1 5))")
    assert conn.execute("SELECT ST_NDims(geom) FROM geo.admin_units WHERE unit_id='p3d'").fetchone()[0] == 2


def test_projected_input_transformed_to_4326(conn):
    utm = conn.execute("SELECT ST_AsEWKT(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 32644))",
                       (json.dumps(TOWN_A),)).fetchone()[0]
    assert utm.startswith("SRID=32644;")
    upsert(conn, "utm-town", utm)
    ext = conn.execute("SELECT ST_SRID(geom), ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)"
                       " FROM geo.admin_units WHERE unit_id='utm-town'").fetchone()
    assert ext[0] == 4326
    assert ext[1:] == pytest.approx(EXPECTED_EXTENT["town-a"], abs=1e-6)


@pytest.mark.parametrize("ewkt, msg", [
    ("POLYGON((1 1,2 1,2 2,1 2,1 1))", "no SRID"),
    ("SRID=4326;POLYGON((0 0,1 1,1 0,0 1,0 0))", "invalid geometry"),  # bow-tie
    ("SRID=4326;POINT(1 1)", "expected Polygon"),
    ("SRID=4326;POLYGON EMPTY", "empty"),
    ("SRID=4326;LINESTRING(0 0,1 1)", "expected Polygon"),
])
def test_invalid_geometries_rejected(conn, ewkt, msg):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match=msg):
        upsert(conn, "bad", ewkt)


def test_repair_is_explicit_opt_in(conn):
    upsert(conn, "bowtie", "SRID=4326;POLYGON((0 0,1 1,1 0,0 1,0 0))", repair=True)
    valid, n = conn.execute("SELECT ST_IsValid(geom), ST_NumGeometries(geom) FROM geo.admin_units"
                            " WHERE unit_id='bowtie'").fetchone()
    assert valid and n == 2


def test_direct_insert_typmod(conn):
    # PostGIS >= 3.6 promotes Polygon -> MultiPolygon at the typmod; older versions reject it.
    # Either way no non-MultiPolygon value can be stored.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        conn.execute("INSERT INTO geo.admin_units (unit_id, name, geom)"
                     " VALUES ('x', 'X', 'SRID=4326;LINESTRING(1 1,2 2)')")
    conn.rollback()
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        conn.execute("INSERT INTO geo.admin_units (unit_id, name, geom)"
                     " VALUES ('x', 'X', 'SRID=3857;MULTIPOLYGON(((1 1,2 1,2 2,1 2,1 1)))')")


def test_direct_insert_of_invalid_multipolygon_blocked(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO geo.admin_units (unit_id, name, geom)"
                     " VALUES ('x', 'X', 'SRID=4326;MULTIPOLYGON(((0 0,1 1,1 0,0 1,0 0)))')")


def test_unit_owned_by_other_dataset_not_overwritten(conn):
    with pytest.raises(psycopg.errors.UniqueViolation, match="another dataset"):
        upsert(conn, "town-a", "SRID=4326;POLYGON((1 1,2 1,2 2,1 2,1 1))")


def test_dataset_reference_must_exist(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        upsert(conn, "orphan", "SRID=4326;POLYGON((1 1,2 1,2 2,1 2,1 1))", dataset=("no-such-dataset", 1))


@pytest.mark.parametrize("bbox, expected", [
    ((78.009, 17.979, 78.021, 17.991), ["town-a"]),
    ((78.029, 17.949, 78.036, 17.956), ["town-b"]),
    ((78.000, 17.940, 78.040, 18.000), ["town-a", "town-b"]),
    ((78.040, 17.960, 78.060, 17.970), []),
    ((78.020, 17.990, 78.025, 17.995), ["town-a"]),  # touches Town A's corner: intersects semantics
])
def test_bbox_selection_one_both_neither(conn, bbox, expected):
    assert [u["unit_id"] for u in queries.units_in_bbox(conn, bbox)] == expected


def test_bbox_in_other_crs(conn):
    merc = conn.execute("SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM"
                        " ST_Transform(ST_MakeEnvelope(78.012, 17.982, 78.018, 17.988, 4326), 3857) e").fetchone()
    assert [u["unit_id"] for u in queries.units_in_bbox(conn, merc, srid=3857)] == ["town-a"]


def test_invalid_bbox_rejected(conn):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        queries.units_in_bbox(conn, (78.02, 17.98, 78.01, 17.99))


def test_intersects_with_explicit_crs_conversion(conn):
    # 300 m circle around Town A's centre, expressed in UTM zone 44N
    centre = conn.execute("SELECT ST_AsGeoJSON(ST_Transform("
                          "ST_SetSRID(ST_MakePoint(78.015, 17.985), 4326), 32644))").fetchone()[0]
    aoi = conn.execute("SELECT ST_AsGeoJSON(ST_Buffer(ST_SetSRID(ST_GeomFromGeoJSON(%s), 32644), 300))",
                       (centre,)).fetchone()[0]
    assert [u["unit_id"] for u in queries.units_intersecting(conn, aoi, crs="EPSG:32644")] == ["town-a"]
    # identical coordinates mislabelled as EPSG:4326 are rejected, not silently unmatched
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="outside EPSG:4326"):
        queries.units_intersecting(conn, aoi, crs="EPSG:4326")


def test_intersects_rejects_unknown_crs(conn):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        queries.units_intersecting(conn, TOWN_A, crs="EPSG:0")


def test_polygon_selection_covered_by(conn):
    covered = conn.execute(
        "SELECT unit_id FROM geo.units_covered_by(ST_MakeEnvelope(78.0, 17.94, 78.03, 18.0, 4326))").fetchall()
    assert covered == [("town-a",)], "Town B only partially inside (x up to 78.035)"


def test_overlap_and_aggregation(conn):
    half_a = {"type": "Polygon", "coordinates": [[[78.015, 17.97], [78.04, 17.97], [78.04, 18.0],
                                                   [78.015, 18.0], [78.015, 17.97]]]}
    rows = {r["unit_id"]: r for r in queries.unit_overlap(conn, half_a)}
    assert set(rows) == {"town-a"}
    assert rows["town-a"]["overlap_fraction"] == pytest.approx(0.5, rel=1e-3)
    # geodesic area of a 0.01 deg cell at ~18 N is ~1.17 km^2
    assert rows["town-a"]["unit_area_m2"] == pytest.approx(1.1717e6, rel=0.01)

    summary = conn.execute("SELECT level, unit_count, total_area_m2, ST_AsText(extent)"
                           " FROM geo.summarize_units()").fetchall()
    assert summary[0][:2] == ("synthetic_town", 2)
    assert summary[0][2] == pytest.approx(1.1717e6 * 1.25, rel=0.01)
    within = conn.execute("SELECT unit_count FROM geo.summarize_units("
                          "ST_MakeEnvelope(78.0, 17.97, 78.03, 18.0, 4326))").fetchall()
    assert within == [(1,)]
