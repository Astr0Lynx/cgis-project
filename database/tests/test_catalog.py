"""catalog.datasets constraints, registration semantics, current_datasets view, concurrency."""

import threading

import psycopg
import pytest
from psycopg.types.json import Jsonb

from cgis_db import queries
from conftest import raster_record

INSERT = (
    "INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, year, crs, bbox,"
    " resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance, status)"
    " VALUES (%(dataset_id)s, %(version)s, %(title)s, %(kind)s, %(product_level)s, %(year)s, %(crs)s,"
    " ST_GeomFromEWKT(%(bbox)s), %(resolution_x)s, %(resolution_y)s, %(units)s, %(nodata)s,"
    " %(asset_path)s, %(checksum_sha256)s, %(source)s, %(provenance)s, %(status)s)"
)
BBOX = "SRID=4326;POLYGON((78 17.936,78.064 17.936,78.064 18,78 18,78 17.936))"


def row(**overrides):
    r = raster_record(bbox=BBOX)
    r.update(overrides)
    r["provenance"] = Jsonb(r["provenance"])
    return r


def test_valid_insert(conn):
    conn.execute(INSERT, row())
    got = conn.execute("SELECT status, created_at = updated_at FROM catalog.datasets"
                       " WHERE dataset_id='synthetic-worldpop-2001'").fetchone()
    assert got == ("ready", True)


@pytest.mark.parametrize("overrides, error", [
    ({"dataset_id": None}, psycopg.errors.NotNullViolation),
    ({"dataset_id": "Bad ID!"}, psycopg.errors.CheckViolation),
    ({"dataset_id": "../etc"}, psycopg.errors.CheckViolation),
    ({"version": 0}, psycopg.errors.CheckViolation),
    ({"version": None}, psycopg.errors.NotNullViolation),
    ({"title": "  "}, psycopg.errors.CheckViolation),
    ({"kind": "table"}, psycopg.errors.CheckViolation),
    ({"product_level": "level3"}, psycopg.errors.CheckViolation),
    ({"status": "published"}, psycopg.errors.CheckViolation),
    ({"year": 99999}, psycopg.errors.CheckViolation),
    ({"crs": "4326"}, psycopg.errors.InvalidParameterValue),  # guard trigger fires before CHECKs
    ({"crs": "EPSG:999999"}, psycopg.errors.InvalidParameterValue),
    ({"resolution_x": 0}, psycopg.errors.CheckViolation),
    ({"resolution_y": -0.001}, psycopg.errors.CheckViolation),
    ({"units": ""}, psycopg.errors.CheckViolation),
    ({"asset_path": "/etc/passwd"}, psycopg.errors.CheckViolation),
    ({"asset_path": "a/../../secret.tif"}, psycopg.errors.CheckViolation),
    ({"asset_path": "https://example.com/x.tif"}, psycopg.errors.CheckViolation),
    ({"asset_path": "C:/data/x.tif"}, psycopg.errors.CheckViolation),
    ({"asset_path": "a\\b.tif"}, psycopg.errors.CheckViolation),
    ({"checksum_sha256": "ABC"}, psycopg.errors.CheckViolation),
    ({"source": ""}, psycopg.errors.CheckViolation),
    # wrong SRID / geometry type rejected by the column typmod
    ({"bbox": "SRID=3857;POLYGON((0 0,1 0,1 1,0 1,0 0))"}, psycopg.errors.InvalidParameterValue),
    ({"bbox": "SRID=4326;POINT(78 18)"}, psycopg.errors.InvalidParameterValue),
    ({"bbox": "SRID=4326;POLYGON((170 0,190 0,190 1,170 1,170 0))"}, psycopg.errors.CheckViolation),
    ({"bbox": "SRID=4326;POLYGON((0 0,1 1,1 0,0 1,0 0))"}, psycopg.errors.CheckViolation),
    # ready requires a complete record
    ({"bbox": None}, psycopg.errors.CheckViolation),
    ({"checksum_sha256": None}, psycopg.errors.CheckViolation),
    ({"asset_path": None}, psycopg.errors.CheckViolation),
    ({"units": None}, psycopg.errors.CheckViolation),
])
def test_constraint_rejections(conn, overrides, error):
    with pytest.raises(error):
        conn.execute(INSERT, row(**overrides))


def test_provenance_must_be_object(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(INSERT, {**row(), "provenance": Jsonb([1, 2])})


def test_staged_rows_may_be_incomplete(conn):
    conn.execute(INSERT, row(status="staged", bbox=None, checksum_sha256=None, asset_path=None, units=None))


def test_projected_native_crs_is_accepted(conn):
    conn.execute(INSERT, row(dataset_id="utm-raster", crs="EPSG:32644", resolution_x=100, resolution_y=100))


def test_duplicate_primary_key_rejected(conn):
    conn.execute(INSERT, row())
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(INSERT, row())


def test_register_is_idempotent_and_rejects_conflicts(conn):
    rec = raster_record()
    assert queries.register_dataset(conn, rec)["outcome"] == "inserted"
    assert queries.register_dataset(conn, rec)["outcome"] == "unchanged"
    # re-registering a ready row as "staged" (importer re-run) must not downgrade it
    assert queries.register_dataset(conn, {**rec, "status": "staged"}) == {
        "dataset_id": rec["dataset_id"], "version": 1, "status": "ready", "outcome": "unchanged"}
    with pytest.raises(queries.CatalogConflict, match="conflicting"):
        queries.register_dataset(conn, {**rec, "checksum_sha256": "b" * 64})


def test_register_ready_metadata_change_is_conflict(conn):
    rec = raster_record()
    queries.register_dataset(conn, rec)
    with pytest.raises(queries.CatalogConflict):
        queries.register_dataset(conn, {**rec, "title": "SYNTHETIC renamed"})


def test_register_staged_then_ready_flow(conn):
    rec = raster_record(status="staged", checksum_sha256=None)
    assert queries.register_dataset(conn, rec)["outcome"] == "inserted"
    out = queries.register_dataset(conn, {**rec, "checksum_sha256": "c" * 64})
    assert out["outcome"] == "updated"
    with pytest.raises(queries.CatalogConflict):
        with conn.transaction():
            queries.register_dataset(conn, {**rec, "checksum_sha256": "d" * 64})
    queries.set_dataset_status(conn, rec["dataset_id"], 1, "ready")
    assert queries.resolve_dataset(conn, rec["dataset_id"])["checksum_sha256"] == "c" * 64


@pytest.mark.parametrize("bbox", [
    [78.0, 17.936, 78.064, 18.0],
    {"type": "Polygon", "coordinates": [[[78, 17.936], [78.064, 17.936], [78.064, 18], [78, 18], [78, 17.936]]]},
    BBOX,
])
def test_register_bbox_forms(conn, bbox):
    queries.register_dataset(conn, raster_record(bbox=bbox))
    ext = conn.execute("SELECT ST_XMin(bbox), ST_YMin(bbox), ST_XMax(bbox), ST_YMax(bbox), ST_SRID(bbox)"
                       " FROM catalog.datasets WHERE dataset_id='synthetic-worldpop-2001'").fetchone()
    assert ext == pytest.approx((78.0, 17.936, 78.064, 18.0, 4326))


def test_register_projected_bbox_is_transformed(conn):
    ewkt = conn.execute(
        "SELECT ST_AsEWKT(ST_Transform(ST_MakeEnvelope(78.0, 17.936, 78.064, 18.0, 4326), 3857))").fetchone()[0]
    queries.register_dataset(conn, raster_record(bbox=ewkt))
    assert conn.execute("SELECT ST_SRID(bbox) FROM catalog.datasets").fetchone()[0] == 4326


@pytest.mark.parametrize("bad, error", [
    ({"bbox": [1, 2, 3]}, psycopg.errors.InvalidParameterValue),
    ({"bbox": [3, 0, 1, 1]}, psycopg.errors.InvalidParameterValue),
    ({"bbox": "POLYGON((0 0,1 0,1 1,0 1,0 0))"}, psycopg.errors.InvalidParameterValue),  # no SRID
    ({"kind": "image"}, psycopg.errors.CheckViolation),
    ({"dataset_id": None}, psycopg.errors.NotNullViolation),
])
def test_register_rejects_bad_input(conn, bad, error):
    with pytest.raises(error):
        queries.register_dataset(conn, raster_record(**bad))


def test_current_view_selects_highest_ready_version(conn):
    did = "synthetic-worldpop-2011"
    reg = lambda v, status, ck: queries.register_dataset(  # noqa: E731
        conn, raster_record(dataset_id=did, version=v, status=status, checksum_sha256=ck * 64, year=2011))
    current = lambda: (queries.resolve_dataset(conn, did) or {}).get("version")  # noqa: E731

    reg(1, "ready", "1")
    reg(2, "staged", "2")
    assert current() == 1, "staged versions are not visible"
    queries.set_dataset_status(conn, did, 2, "ready")
    assert current() == 2
    reg(3, "failed", "3")
    assert current() == 2, "failed versions are not visible"
    queries.set_dataset_status(conn, did, 2, "failed")  # withdraw v2
    assert current() == 1
    assert conn.execute("SELECT count(*) FROM catalog.datasets WHERE dataset_id=%s", (did,)).fetchone()[0] == 3


def test_ready_rows_are_immutable(conn):
    queries.register_dataset(conn, raster_record())
    for stmt in [
        "UPDATE catalog.datasets SET title = 'x' WHERE dataset_id='synthetic-worldpop-2001'",
        "UPDATE catalog.datasets SET asset_path = 'other.tif' WHERE dataset_id='synthetic-worldpop-2001'",
        "UPDATE catalog.datasets SET version = 9 WHERE dataset_id='synthetic-worldpop-2001'",
        "UPDATE catalog.datasets SET status = 'staged' WHERE dataset_id='synthetic-worldpop-2001'",
    ]:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            with conn.transaction():  # savepoint inside the test transaction
                conn.execute(stmt)
    # withdrawal (ready -> failed) is the one permitted change
    queries.set_dataset_status(conn, "synthetic-worldpop-2001", 1, "failed")


def test_failed_rows_can_be_restaged_not_published_directly(conn):
    queries.register_dataset(conn, raster_record(status="failed"))
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        queries.set_dataset_status(conn, "synthetic-worldpop-2001", 1, "ready")


def test_timestamps_maintained(conn):
    queries.register_dataset(conn, raster_record(status="staged"))
    created = conn.execute("SELECT created_at FROM catalog.datasets"
                           " WHERE dataset_id = 'synthetic-worldpop-2001'").fetchone()[0]
    conn.execute("UPDATE catalog.datasets SET created_at = '2000-01-01', description = 'd'"
                 " WHERE dataset_id = 'synthetic-worldpop-2001'")
    c2, u2 = conn.execute("SELECT created_at, updated_at FROM catalog.datasets"
                          " WHERE dataset_id = 'synthetic-worldpop-2001'").fetchone()
    assert c2 == created and u2.tzinfo is not None


def test_bbox_discovery(conn):
    queries.register_dataset(conn, raster_record())
    assert {d["dataset_id"] for d in queries.datasets_in_bbox(conn, (78.01, 17.98, 78.02, 17.99))} == {
        "synthetic-worldpop-2001", "synthetic-towns"}
    assert [d["dataset_id"] for d in queries.datasets_in_bbox(conn, (78.01, 17.98, 78.02, 17.99), kind="raster")
            ] == ["synthetic-worldpop-2001"]
    assert queries.datasets_in_bbox(conn, (10, 10, 11, 11)) == []
    # same area expressed in Web Mercator
    merc = conn.execute("SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM"
                        " ST_Transform(ST_MakeEnvelope(78.01,17.98,78.02,17.99,4326),3857) e").fetchone()
    assert len(queries.datasets_in_bbox(conn, merc, srid=3857, kind="raster")) == 1


def test_sql_injection_is_inert(conn):
    hostile = "x'; DROP TABLE catalog.datasets; --"
    assert queries.resolve_dataset(conn, hostile) is None
    with pytest.raises(psycopg.errors.CheckViolation):
        queries.register_dataset(conn, raster_record(dataset_id=hostile))
    conn.rollback()
    assert conn.execute("SELECT to_regclass('catalog.datasets') IS NOT NULL").fetchone()[0]


def test_concurrent_registration_single_winner(migrated_dsn):
    rec = raster_record()
    barrier = threading.Barrier(6)
    outcomes, errors = [], []

    def worker(checksum):
        try:
            with psycopg.connect(migrated_dsn) as c:
                barrier.wait()
                outcomes.append(queries.register_dataset(c, {**rec, "checksum_sha256": checksum})["outcome"])
        except queries.CatalogConflict:
            errors.append(checksum)

    # 4 identical registrations + 2 conflicting ones race on the same (id, version)
    threads = [threading.Thread(target=worker, args=(ck,)) for ck in ["a" * 64] * 4 + ["f" * 64] * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with psycopg.connect(migrated_dsn) as c:
        winner = c.execute("SELECT checksum_sha256 FROM catalog.datasets").fetchall()
    assert len(winner) == 1
    assert outcomes.count("inserted") == 1
    assert set(outcomes) <= {"inserted", "unchanged"}
    loser_ck = "f" * 64 if winner[0][0] == "a" * 64 else "a" * 64
    assert errors and set(errors) == {loser_ck}
    assert len(outcomes) + len(errors) == 6
