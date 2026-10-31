"""Catalog, boundaries and seeding (TEST_PLAN 2-3; ACCEPTANCE P0 2-5)."""

from __future__ import annotations

import json
import os
import re

import pytest

from conftest import RASTER_2001, RASTER_2011, RASTERS, STATE, TOWNS

FIXTURE_IDS = (RASTER_2001, RASTER_2011, TOWNS)


def rows(db, sql, params=()):
    cur = db.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip().startswith("{")]


def test_each_fixture_registered_once_and_ready(db):
    got = rows(db, "SELECT dataset_id, version, status FROM catalog.datasets WHERE dataset_id = ANY(%s) "
                   "ORDER BY dataset_id, version", (list(FIXTURE_IDS),))
    assert [(r["dataset_id"], r["version"], r["status"]) for r in got] == [
        (TOWNS, 1, "ready"), (RASTER_2001, 1, "ready"), (RASTER_2011, 1, "ready")]


def test_current_view_exposes_same_columns_as_table(db):
    table = [r["column_name"] for r in rows(db, "SELECT column_name FROM information_schema.columns "
                                                "WHERE table_schema='catalog' AND table_name='datasets' ORDER BY ordinal_position")]
    view = [r["column_name"] for r in rows(db, "SELECT column_name FROM information_schema.columns "
                                               "WHERE table_schema='catalog' AND table_name='current_datasets' ORDER BY ordinal_position")]
    assert view == table
    for col in ("dataset_id", "version", "title", "kind", "product_level", "year", "crs", "bbox", "resolution_x",
                "resolution_y", "units", "nodata", "asset_path", "checksum_sha256", "source", "provenance",
                "status", "created_at", "updated_at"):
        assert col in table


@pytest.mark.parametrize("year", [2001, 2011])
def test_raster_catalog_metadata(db, year):
    (r,) = rows(db, "SELECT *, ST_XMin(bbox) w, ST_YMin(bbox) s, ST_XMax(bbox) e, ST_YMax(bbox) n, ST_SRID(bbox) srid "
                    "FROM catalog.current_datasets WHERE dataset_id = %s", (RASTERS[year],))
    assert r["kind"] == "raster" and r["product_level"] == "source" and r["year"] == year
    assert r["crs"] == "EPSG:4326" and r["units"] == "people_per_cell" and r["nodata"] == -9999
    assert r["resolution_x"] == pytest.approx(0.001) and r["resolution_y"] == pytest.approx(0.001)
    assert (r["w"], r["s"], r["e"], r["n"]) == pytest.approx((78.0, 17.936, 78.064, 18.0), abs=1e-9)
    assert r["srid"] == 4326
    assert re.fullmatch(r"[0-9a-f]{64}", r["checksum_sha256"])
    path = r["asset_path"]
    assert path and not path.startswith("/") and ".." not in path.split("/")
    assert "SYNTHETIC" in r["title"]
    prov = json.dumps(r["provenance"])
    assert "deterministic-fixture" in prov or "SYNTHETIC" in prov
    assert r["source"]
    assert r["created_at"].tzinfo is not None and r["updated_at"].tzinfo is not None


def test_vector_catalog_metadata(db):
    (r,) = rows(db, "SELECT *, ST_XMin(bbox) w, ST_YMin(bbox) s, ST_XMax(bbox) e, ST_YMax(bbox) n "
                    "FROM catalog.current_datasets WHERE dataset_id = %s", (TOWNS,))
    assert r["kind"] == "vector" and r["crs"] == "EPSG:4326"
    assert (r["w"], r["s"], r["e"], r["n"]) == pytest.approx((78.010, 17.950, 78.035, 17.990), abs=1e-9)
    assert "SYNTHETIC" in r["title"]


def test_admin_units_are_multipolygons_with_exact_extents(db):
    got = rows(db, "SELECT unit_id, name, level, GeometryType(geom) gt, ST_SRID(geom) srid, ST_IsValid(geom) ok, "
                   "ST_XMin(geom) w, ST_YMin(geom) s, ST_XMax(geom) e, ST_YMax(geom) n "
                   "FROM geo.admin_units WHERE unit_id IN ('town-a','town-b') ORDER BY unit_id")
    assert [(r["unit_id"], r["name"], r["level"], r["gt"], r["srid"], r["ok"]) for r in got] == [
        ("town-a", "Town A", "synthetic_town", "MULTIPOLYGON", 4326, True),
        ("town-b", "Town B", "synthetic_town", "MULTIPOLYGON", 4326, True)]
    assert (got[0]["w"], got[0]["s"], got[0]["e"], got[0]["n"]) == pytest.approx((78.010, 17.980, 78.020, 17.990))
    assert (got[1]["w"], got[1]["s"], got[1]["e"], got[1]["n"]) == pytest.approx((78.030, 17.950, 78.035, 17.955))


def test_admin_units_linked_to_catalog_dataset(db):
    got = rows(db, "SELECT unit_id, dataset_id, dataset_version FROM geo.admin_units WHERE unit_id IN ('town-a','town-b')")
    assert {(r["dataset_id"], r["dataset_version"]) for r in got} == {(TOWNS, 1)}


def test_gist_indexes_exist(db):
    idx = rows(db, "SELECT schemaname, tablename, indexdef FROM pg_indexes "
                   "WHERE (schemaname, tablename) IN (('geo','admin_units'),('catalog','datasets'))")
    assert any(i["tablename"] == "admin_units" and "USING gist" in i["indexdef"] and "geom" in i["indexdef"] for i in idx)
    assert any(i["tablename"] == "datasets" and "USING gist" in i["indexdef"] and "bbox" in i["indexdef"] for i in idx)


def test_spatial_filter_on_admin_units(db):
    def hits(w, s, e, n):
        return [r["unit_id"] for r in rows(db, "SELECT unit_id FROM geo.admin_units "
                                               "WHERE geom && ST_MakeEnvelope(%s,%s,%s,%s,4326) "
                                               "AND ST_Intersects(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326)) ORDER BY unit_id",
                                           (w, s, e, n, w, s, e, n))]
    assert hits(78.009, 17.979, 78.021, 17.991) == ["town-a"]
    assert hits(78.000, 17.940, 78.064, 18.000) == ["town-a", "town-b"]
    assert hits(79.0, 19.0, 79.1, 19.1) == []


def test_service_roles_are_least_privilege(db):
    ingest, ogc, proc = (os.environ[k] for k in ("INGEST_DB_USER", "OGC_DB_USER", "PROCESSING_DB_USER"))

    def can(role, table, priv):
        return db.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, priv)).fetchone()[0]

    assert can(ogc, "catalog.current_datasets", "SELECT") and not can(ogc, "catalog.datasets", "INSERT")
    assert not can(ogc, "geo.admin_units", "UPDATE") and not can(ogc, "ops.jobs", "INSERT")
    assert can(proc, "ops.jobs", "INSERT") and not can(proc, "catalog.datasets", "UPDATE")
    assert can(ingest, "catalog.datasets", "INSERT") and not can(ingest, "ops.jobs", "INSERT")
    for role in (ingest, ogc, proc):
        su = db.execute("SELECT rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname=%s", (role,)).fetchone()[0]
        assert su is False


def test_seed_twice_is_idempotent():
    first, second = STATE / "seed1.jsonl", STATE / "seed2.jsonl"
    if not (first.exists() and second.exists()):
        pytest.skip("seed outputs are written by run_e2e.sh")
    outcomes1 = {d["dataset_id"]: d["outcome"] for d in read_jsonl(first) if "dataset_id" in d}
    outcomes2 = {d["dataset_id"]: d["outcome"] for d in read_jsonl(second) if "dataset_id" in d}
    assert set(outcomes1) == set(outcomes2) == set(FIXTURE_IDS)
    assert set(outcomes1.values()) <= {"imported", "unchanged"}
    assert set(outcomes2.values()) == {"unchanged"}


def test_generated_fixtures_are_labelled_synthetic():
    manifest = json.loads((STATE.parent / "manifest.json").read_text())
    text = json.dumps(manifest)
    assert "SYNTHETIC" in text and "deterministic-fixture" in text
    towns = json.loads((STATE.parent / "synthetic_towns.geojson").read_text())
    assert {f["properties"]["unit_id"] for f in towns["features"]} == {"town-a", "town-b"}
    assert all(f["properties"]["source"] == "deterministic-fixture" for f in towns["features"])
