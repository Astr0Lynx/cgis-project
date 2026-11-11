# Version-aware geo.admin_units (0007), render profiles (0008) and the async job queue (0009).
# Every test starts from a 0006 database holding v1 towns rows (pre-0007 code path), then upgrades it.

from __future__ import annotations

import json

import psycopg
import pytest

from cgis_db import fixtures, migrate, queries
from conftest import quiet

PRE_VERSIONING = 6
TOWNS = "synthetic-towns"


# The stub towns FeatureCollection with every polygon moved `shift` degrees east, so versions differ.
# Used by the 0007 tests here.
def _towns_shifted(shift: float) -> dict:
    doc = json.loads(fixtures.STUB_GEOJSON.read_text())
    for f in doc["features"]:
        ring = f["geometry"]["coordinates"][0]
        f["geometry"]["coordinates"][0] = [[x + shift, y] for x, y in ring]
    return doc


# Writes `doc` to a temp file and loads it as (dataset_id, version). Used by the 0007 tests here.
def _load(conn, doc: dict, tmp_path, *, dataset_id=TOWNS, version=1) -> dict:
    p = tmp_path / f"{dataset_id}-v{version}-towns.geojson"
    p.write_text(json.dumps(doc))
    return fixtures.load_units(conn, p, dataset_id=dataset_id, version=version)


# Database at schema 0006 holding v1 rows, upgraded to the latest core migration; asserts the
# upgrade left every existing unit row unchanged.
@pytest.fixture
def legacy_dsn(db_factory):
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        migrate.up(c, target=PRE_VERSIONING, log=quiet)
    with psycopg.connect(dsn) as c:
        fixtures.load_units(c)
    with psycopg.connect(dsn, autocommit=True) as c:
        before = c.execute("SELECT unit_id, dataset_id, dataset_version, ST_AsEWKB(geom), properties"
                           " FROM geo.admin_units ORDER BY unit_id").fetchall()
        applied = migrate.up(c, log=quiet)
        assert [m.version for m in applied][0] == PRE_VERSIONING + 1
        after = c.execute("SELECT unit_id, dataset_id, dataset_version, ST_AsEWKB(geom), properties"
                          " FROM geo.admin_units ORDER BY unit_id").fetchall()
        assert after == before, "upgrade must not change any existing unit row"
    return dsn


# Autocommit connection to the upgraded legacy database.
@pytest.fixture
def upgraded(legacy_dsn):
    with psycopg.connect(legacy_dsn, autocommit=True) as c:
        yield c


# ---------------------------------------------------------------------------- 0007 admin units

# The upgrade moves the PK to unit_key and adds the (dataset_id, dataset_version, unit_id) identity.
def test_upgrade_keys_identity_by_dataset_version(upgraded):
    c = upgraded
    pk = c.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                   " WHERE conrelid = 'geo.admin_units'::regclass AND contype = 'p'").fetchone()[0]
    assert pk == "PRIMARY KEY (unit_key)"
    ident = c.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                      " WHERE conname = 'admin_units_identity'").fetchone()[0]
    assert ident == "UNIQUE NULLS NOT DISTINCT (dataset_id, dataset_version, unit_id)"
    assert c.execute("SELECT count(*), count(unit_key) FROM geo.admin_units").fetchone() == (2, 2)
    assert [u["unit_id"] for u in queries.dataset_units(c, TOWNS)] == ["town-a", "town-b"]


# Importing v2 keeps v1; current view, bbox primitive and unit_versions follow the highest ready version.
def test_reimport_new_version_keeps_old_version(upgraded, tmp_path):
    c = upgraded
    old_a = c.execute("SELECT ST_XMin(geom) FROM geo.admin_units WHERE unit_id='town-a'").fetchone()[0]
    out = _load(c, _towns_shifted(0.5), tmp_path, version=2)
    assert out["outcome"] == "inserted" and out["units"] == 2

    v1 = queries.dataset_units(c, TOWNS, 1)
    v2 = queries.dataset_units(c, TOWNS, 2)
    assert [u["unit_id"] for u in v1] == [u["unit_id"] for u in v2] == ["town-a", "town-b"]
    assert min(p[0] for p in v1[0]["geom"]["coordinates"][0][0]) == pytest.approx(old_a)
    assert min(p[0] for p in v2[0]["geom"]["coordinates"][0][0]) == pytest.approx(old_a + 0.5)
    assert c.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0] == 4, "no data loss"

    # current view / default resolution / spatial primitives follow the highest ready version
    assert {u["dataset_version"] for u in queries.dataset_units(c, TOWNS)} == {2}
    assert c.execute("SELECT count(*), min(dataset_version) FROM geo.current_admin_units").fetchone() == (2, 2)
    old_bbox = (78.009, 17.979, 78.021, 17.991)
    assert queries.units_in_bbox(c, old_bbox) == []
    moved = queries.units_in_bbox(c, (78.509, 17.979, 78.521, 17.991))
    assert [(u["unit_id"], u["dataset_version"]) for u in moved] == [("town-a", 2)]

    hist = queries.unit_versions(c, TOWNS, "town-a")
    assert [(h["dataset_version"], h["catalog_status"], h["is_current"]) for h in hist] == [
        (2, "ready", True), (1, "ready", False)]


# Withdrawing v2 (ready -> failed) serves v1 again while v2 rows stay inspectable.
def test_withdrawn_version_falls_back_to_previous(upgraded, tmp_path):
    c = upgraded
    _load(c, _towns_shifted(0.5), tmp_path, version=2)
    queries.set_dataset_status(c, TOWNS, 2, "failed")  # withdrawal
    assert {u["dataset_version"] for u in queries.dataset_units(c, TOWNS)} == {1}
    assert [u["unit_id"] for u in queries.units_in_bbox(c, (78.009, 17.979, 78.021, 17.991))] == ["town-a"]
    # the withdrawn version's rows are kept and still inspectable explicitly
    assert len(queries.dataset_units(c, TOWNS, 2)) == 2
    assert c.execute("SELECT count(*) FROM geo.current_admin_units").fetchone()[0] == 2


# The same unit_id can exist in two datasets.
def test_same_unit_id_in_two_datasets(upgraded, tmp_path):
    c = upgraded
    _load(c, _towns_shifted(1.0), tmp_path, dataset_id="other-boundaries", version=1)
    rows = c.execute("SELECT dataset_id, dataset_version FROM geo.admin_units WHERE unit_id='town-a'"
                     " ORDER BY dataset_id").fetchall()
    assert rows == [("other-boundaries", 1), (TOWNS, 1)]
    assert {u["dataset_id"] for u in queries.units_in_bbox(c, (78.0, 17.9, 79.1, 18.0))} == {
        TOWNS, "other-boundaries"}
    assert [u["unit_id"] for u in queries.dataset_units(c, "other-boundaries")] == ["town-a", "town-b"]


# Identity is unique per version, and unique among dataset-less rows (NULLS NOT DISTINCT).
def test_identity_is_unique_per_version(upgraded):
    c = upgraded
    with pytest.raises(psycopg.errors.UniqueViolation):
        c.execute("INSERT INTO geo.admin_units (unit_id, name, geom, dataset_id, dataset_version)"
                  " SELECT unit_id, name, geom, dataset_id, dataset_version FROM geo.admin_units"
                  " WHERE unit_id='town-a'")
    c.execute("INSERT INTO geo.admin_units (unit_id, name, geom) VALUES"
              " ('loose', 'Loose', 'SRID=4326;MULTIPOLYGON(((1 1,2 1,2 2,1 2,1 1)))')")
    with pytest.raises(psycopg.errors.UniqueViolation):  # NULLS NOT DISTINCT: dataset-less ids stay unique
        c.execute("INSERT INTO geo.admin_units (unit_id, name, geom) VALUES"
                  " ('loose', 'Loose 2', 'SRID=4326;MULTIPOLYGON(((1 1,2 1,2 2,1 2,1 1)))')")


# Units of a ready version cannot be edited, but an identical re-import is allowed.
def test_units_of_ready_version_are_immutable(upgraded):
    c = upgraded
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="immutable"):
        c.execute("UPDATE geo.admin_units SET name = 'Renamed' WHERE unit_id = 'town-a'")
    # an identical rewrite (idempotent re-import) is allowed
    assert fixtures.load_units(c)["outcome"] == "unchanged"
    assert c.execute("SELECT name FROM geo.admin_units WHERE unit_id='town-a'").fetchone()[0] == "Town A"


# Rolling back 0007 refuses while a unit_id is stored twice, then succeeds once duplicates are removed.
def test_rollback_refuses_while_versions_coexist_then_restores(upgraded, tmp_path):
    c = upgraded
    _load(c, _towns_shifted(0.5), tmp_path, version=2)
    with pytest.raises(psycopg.errors.UniqueViolation, match="cannot roll back 0007"):
        migrate.down(c, target=PRE_VERSIONING, log=quiet)
    assert c.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0] == 4, "refusal changed nothing"
    # an operator explicitly removes the superseded version, then the rollback succeeds
    c.execute("DELETE FROM geo.admin_units WHERE dataset_version = 2")
    migrate.down(c, target=PRE_VERSIONING, log=quiet)
    pk = c.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                   " WHERE conrelid = 'geo.admin_units'::regclass AND contype = 'p'").fetchone()[0]
    assert pk == "PRIMARY KEY (unit_id)"
    assert c.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0] == 2
    migrate.up(c, log=quiet)  # and forward again


# With 14,400 units the bbox query uses the GiST index and a collection page uses the identity index.
def test_spatial_query_uses_gist_index_at_scale(upgraded):
    c = upgraded
    c.execute("""
        INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, crs, bbox,
                                      checksum_sha256, source, status)
        VALUES ('bench-grid', 1, 'SYNTHETIC grid', 'vector', 'other', 'EPSG:4326',
                ST_MakeEnvelope(70, 10, 71.2, 11.2, 4326), repeat('b', 64), 'test', 'staged');
        INSERT INTO geo.admin_units (unit_id, name, level, geom, dataset_id, dataset_version)
        SELECT format('g-%s-%s', i, j), 'cell', 'grid',
               ST_Multi(ST_MakeEnvelope(70 + i * 0.01, 10 + j * 0.01, 70.01 + i * 0.01, 10.01 + j * 0.01, 4326)),
               'bench-grid', 1
          FROM generate_series(0, 119) i, generate_series(0, 119) j;
        UPDATE catalog.datasets SET status = 'ready' WHERE dataset_id = 'bench-grid';
        ANALYZE geo.admin_units; ANALYZE catalog.datasets;
    """)
    assert c.execute("SELECT count(*) FROM geo.dataset_units('bench-grid')").fetchone()[0] == 14_400
    plan = "\n".join(r[0] for r in c.execute(
        "EXPLAIN SELECT unit_id FROM geo.current_admin_units u"
        " WHERE u.geom && ST_MakeEnvelope(70.1, 10.1, 70.2, 10.2, 4326)").fetchall())
    assert "admin_units_geom_gist" in plan
    page = "\n".join(r[0] for r in c.execute(
        "EXPLAIN SELECT unit_id FROM geo.admin_units WHERE dataset_id = 'bench-grid' AND dataset_version = 1"
        " ORDER BY unit_id OFFSET 5000 LIMIT 100").fetchall())
    assert "admin_units_identity" in page


# ---------------------------------------------------------------------------- 0008 render profiles

# Registers a ready raster catalog row. Used by the render-profile tests here.
def _raster(conn, dataset_id, *, level="level1", units="people_per_cell"):
    queries.register_dataset(conn, {
        "dataset_id": dataset_id, "version": 1, "title": f"TEST {dataset_id}", "kind": "raster",
        "product_level": level, "year": 2001, "crs": "EPSG:4326", "bbox": [78, 17, 79, 18],
        "resolution_x": 0.001, "resolution_y": 0.001, "units": units, "nodata": -1,
        "asset_path": f"rasters/{dataset_id}.tif", "checksum_sha256": "c" * 64, "source": "test",
        "provenance": {}, "status": "ready"})


# Level-1 rasters of different years share one profile and scale.
def test_render_profiles_share_one_scale_across_years(upgraded):
    c = upgraded
    for year in (2001, 2011):
        _raster(c, f"india-l1-pop-{year}")
    profiles = [queries.render_profile_for(c, f"india-l1-pop-{y}") for y in (2001, 2011)]
    assert {p["profile_id"] for p in profiles} == {"level1-people-per-cell"}
    assert (profiles[0]["rescale_min"], profiles[0]["rescale_max"]) == (0, 50)


# A profile matches by level/id regex only when units are equal; otherwise None (autoscale).
@pytest.mark.parametrize("dataset_id, level, units, expected", [
    ("india-l2-pop-2011-warangal", "level2", "people_per_cell", "level2-people-per-cell"),
    ("india-worldpop-unconstrained-2011-hyderabad-region", "source", "people_per_cell",
     "worldpop-people-per-cell"),
    ("india-ghsl-built-nres-2005", "source", "m2_per_cell", "ghsl-built-m2-per-cell"),
    ("india-l1-pop-2003", "level1", "people_per_km2", None),       # units differ: never a count scale
    ("synthetic-worldpop-2001", "source", "people_per_cell", None),  # no profile: per-dataset autoscale
])
def test_render_profile_matching_requires_equal_units(upgraded, dataset_id, level, units, expected):
    _raster(upgraded, dataset_id, level=level, units=units)
    got = queries.render_profile_for(upgraded, dataset_id)
    assert (got["profile_id"] if got else None) == expected


# Bad rescale range, invalid regex and a profile without any matcher are rejected.
def test_render_profile_validation(upgraded):
    c = upgraded
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO catalog.render_profiles (profile_id, match_product_level, units, rescale_min,"
                  " rescale_max, title, description) VALUES ('bad-range', 'level1', 'x', 5, 1, 't', 'd')")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="regex"):
        c.execute("INSERT INTO catalog.render_profiles (profile_id, match_dataset_regex, units, rescale_min,"
                  " rescale_max, title, description) VALUES ('bad-re', '([', 'x', 0, 1, 't', 'd')")
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO catalog.render_profiles (profile_id, units, rescale_min, rescale_max, title,"
                  " description) VALUES ('no-matcher', 'x', 0, 1, 't', 'd')")


# ---------------------------------------------------------------------------- 0009 job queue

# Async columns default correctly, running -> queued retry is allowed, execution is immutable, sync jobs unchanged.
def test_async_job_columns_and_retry_transition(upgraded):
    c = upgraded
    job = c.execute("INSERT INTO ops.jobs (kind, execution, max_attempts) VALUES ('zonal-statistics', 'async', 3)"
                    " RETURNING job_id, attempts, cancel_requested").fetchone()
    assert job[1:] == (0, False)
    c.execute("UPDATE ops.jobs SET status='running', attempts=attempts+1, heartbeat_at=now(), progress=0.4"
              " WHERE job_id=%s", (job[0],))
    c.execute("UPDATE ops.jobs SET status='queued', worker_id=NULL WHERE job_id=%s", (job[0],))  # retry
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="immutable"):
        c.execute("UPDATE ops.jobs SET execution='sync' WHERE job_id=%s", (job[0],))
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("UPDATE ops.jobs SET progress=1.5 WHERE job_id=%s", (job[0],))
    # pre-0009 style rows still work unchanged
    legacy = queries.create_job(c, "clip", {"dataset_id": "x"})
    queries.start_job(c, legacy)
    queries.complete_job(c, legacy, result={"ok": True})
    row = queries.get_job(c, legacy)
    assert row["status"] == "completed" and row["execution"] == "sync" and row["max_attempts"] == 1


# Rolling back 0009 refuses while async jobs are active.
def test_job_queue_rollback_refuses_active_async_jobs(upgraded):
    c = upgraded
    c.execute("INSERT INTO ops.jobs (kind, execution) VALUES ('clip', 'async')")
    with pytest.raises(psycopg.errors.ObjectInUse):
        migrate.down(c, target=8, log=quiet)
    c.execute("UPDATE ops.jobs SET status='failed', error='cancelled' WHERE execution='async'")
    migrate.down(c, target=8, log=quiet)
    migrate.up(c, log=quiet)
