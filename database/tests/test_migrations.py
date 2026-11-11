# Migration runner: fresh apply, idempotency, concurrency, rollback, drift and contract schema introspection.

import shutil
import threading

import psycopg
import pytest

from cgis_db import migrate
from conftest import quiet

CONTRACT_DATASET_COLUMNS = [
    ("dataset_id", "text"), ("version", "integer"), ("title", "text"), ("description", "text"),
    ("kind", "text"), ("product_level", "text"), ("year", "integer"), ("crs", "text"),
    ("bbox", "geometry"), ("resolution_x", "double precision"), ("resolution_y", "double precision"),
    ("units", "text"), ("nodata", "double precision"), ("asset_path", "text"),
    ("checksum_sha256", "text"), ("source", "text"), ("provenance", "jsonb"), ("status", "text"),
    ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone"),
]


# Column (name, data_type, udt_name) list of a table/view. Used by test_contract_columns_and_view_match.
def _columns(c, schema, table):
    return c.execute(
        "SELECT column_name, data_type, udt_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table)).fetchall()


# Replaces USER-DEFINED data types with their udt name (e.g. geometry). Used with _columns.
def _norm(cols):
    return [(n, u if t == "USER-DEFINED" else t) for n, t, u in cols]


# A second `up` applies nothing and all core migrations show as applied.
def test_fresh_migrate_twice_is_idempotent(fresh_dsn):
    with psycopg.connect(fresh_dsn, autocommit=True) as c:
        first = migrate.up(c, log=quiet)
        assert [m.version for m in first] == list(range(1, len(migrate.discover()) + 1))
        assert migrate.up(c, log=quiet) == []
        states = {(r["track"], r["state"]) for r in migrate.status(c)}
        assert ("core", "applied") in states
        assert all(r["state"] == "applied" for r in migrate.status(c) if r["track"] == "core")


# Every up script can be re-executed by hand (IF NOT EXISTS / OR REPLACE).
def test_migration_sql_itself_is_rerunnable(migrated_dsn):
    with psycopg.connect(migrated_dsn, autocommit=True) as c:
        for m in migrate.discover():
            with c.transaction():
                c.execute(m.up_sql)


# Three concurrent runners: one applies everything, the others nothing.
def test_concurrent_runners_serialise(fresh_dsn):
    errors, applied_counts = [], []

    def run():
        try:
            with psycopg.connect(fresh_dsn, autocommit=True) as c:
                applied_counts.append(len(migrate.up(c, log=quiet)))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(applied_counts) == [0, 0, len(migrate.discover())]


# Column names/types of datasets, current_datasets, admin_units and jobs match the contract.
def test_contract_columns_and_view_match(migrated_dsn):
    with psycopg.connect(migrated_dsn) as c:
        table = _norm(_columns(c, "catalog", "datasets"))
        view = _norm(_columns(c, "catalog", "current_datasets"))
        assert table == CONTRACT_DATASET_COLUMNS
        assert view == table, "current_datasets must expose the same public columns as datasets"

        bbox_type = c.execute(
            "SELECT type, srid FROM geometry_columns WHERE f_table_schema='catalog' AND f_table_name='datasets'"
        ).fetchone()
        assert bbox_type == ("POLYGON", 4326)

        pk = c.execute(
            "SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_index i"
            " JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum"
            " WHERE i.indrelid = 'catalog.datasets'::regclass AND i.indisprimary").fetchone()[0]
        assert pk == ["dataset_id", "version"]

        units = dict((n, t) for n, t in _norm(_columns(c, "geo", "admin_units")))
        assert units["unit_id"] == "text" and units["name"] == "text" and units["level"] == "text"
        assert c.execute(
            "SELECT type, srid FROM geometry_columns WHERE f_table_schema='geo' AND f_table_name='admin_units'"
        ).fetchone() == ("MULTIPOLYGON", 4326)

        jobs = dict(_norm(_columns(c, "ops", "jobs")))
        for col, typ in [("job_id", "uuid"), ("kind", "text"), ("status", "text"), ("input", "jsonb"),
                         ("result_path", "text"), ("error", "text"),
                         ("created_at", "timestamp with time zone"), ("updated_at", "timestamp with time zone")]:
            assert jobs[col] == typ


# The bbox and geom GiST indexes exist.
def test_gist_indexes_exist(migrated_dsn):
    with psycopg.connect(migrated_dsn) as c:
        rows = c.execute(
            "SELECT schemaname || '.' || indexname FROM pg_indexes WHERE indexdef ILIKE '%USING gist%'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert {"catalog.datasets_bbox_gist", "geo.admin_units_geom_gist"} <= names


# down to 0 removes all schemas and PostGIS; up re-applies everything.
def test_full_rollback_and_reapply(migrated_dsn):
    with psycopg.connect(migrated_dsn, autocommit=True) as c:
        rolled = migrate.down(c, target=0, log=quiet)
        assert [m.version for m in rolled] == list(range(len(migrate.discover()), 0, -1))
        assert c.execute("SELECT to_regnamespace('catalog'), to_regnamespace('geo'), to_regnamespace('ops')"
                         ).fetchone() == (None, None, None)
        assert c.execute("SELECT count(*) FROM pg_extension WHERE extname='postgis'").fetchone()[0] == 0
        assert len(migrate.up(c, log=quiet)) == len(migrate.discover())


# down --steps, up --target and the up-below-current error.
def test_step_down_and_target(migrated_dsn):
    with psycopg.connect(migrated_dsn, autocommit=True) as c:
        n = len(migrate.discover())
        assert [m.version for m in migrate.down(c, steps=2, log=quiet)] == [n, n - 1]
        assert max(migrate.applied(c, "core")) == n - 2
        assert [m.version for m in migrate.up(c, target=n - 1, log=quiet)] == [n - 1]
        with pytest.raises(migrate.MigrationError):
            migrate.up(c, target=n - 2, log=quiet)
        migrate.up(c, log=quiet)


# down never CASCADE-drops a foreign table that uses PostGIS.
def test_rollback_never_cascades_into_foreign_objects(migrated_dsn):
    with psycopg.connect(migrated_dsn, autocommit=True) as c:
        c.execute("CREATE TABLE public.someone_elses (g geometry(Point, 4326))")
        c.execute("INSERT INTO public.someone_elses VALUES ('SRID=4326;POINT(78 18)')")
        with pytest.raises(psycopg.errors.DependentObjectsStillExist):
            migrate.down(c, target=0, log=quiet)
        assert migrate.applied(c, "core").keys() == {1}  # 0002..0006 rolled back, 0001 kept
        assert c.execute("SELECT count(*) FROM public.someone_elses").fetchone()[0] == 1
        c.execute("DROP TABLE public.someone_elses")
        migrate.down(c, target=0, log=quiet)
        assert migrate.applied(c, "core") == {}


# Editing an applied migration is reported as drift.
def test_checksum_drift_detected(migrated_dsn, tmp_path, monkeypatch):
    src = migrate.migrations_dir()
    copy = tmp_path / "migrations"
    shutil.copytree(src, copy)
    target = copy / "core" / "0005_ops.up.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    monkeypatch.setenv(migrate.MIGRATIONS_ENV, str(copy))
    with psycopg.connect(migrated_dsn, autocommit=True) as c:
        with pytest.raises(migrate.MigrationError, match="drift"):
            migrate.up(c, log=quiet)
        assert any(r["state"] == "DRIFT" for r in migrate.status(c))


# CRLF line endings do not count as drift.
def test_crlf_checkout_does_not_cause_drift(migrated_dsn, tmp_path, monkeypatch):
    copy = tmp_path / "migrations"
    shutil.copytree(migrate.migrations_dir(), copy)
    for p in copy.rglob("*.sql"):
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setenv(migrate.MIGRATIONS_ENV, str(copy))
    with psycopg.connect(migrated_dsn, autocommit=True) as c:
        assert migrate.up(c, log=quiet) == []


# raster_demo refuses to apply before core.
def test_optional_track_requires_core(fresh_dsn):
    with psycopg.connect(fresh_dsn, autocommit=True) as c:
        with pytest.raises(migrate.MigrationError, match="requires core"):
            migrate.up(c, "raster_demo", log=quiet)


# The runner refuses a non-autocommit connection.
def test_runner_requires_autocommit(fresh_dsn):
    with psycopg.connect(fresh_dsn) as c:
        with pytest.raises(migrate.MigrationError, match="autocommit"):
            migrate.up(c, log=quiet)
