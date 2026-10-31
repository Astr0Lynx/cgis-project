"""Importer <-> PostGIS catalog contract tests. Require INGESTION_TEST_DSN (see conftest)."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from ingestion import cli, fixtures
from ingestion.errors import AssetError, ConflictError, IngestionError, ValidationError
from ingestion.manifest import load_manifest, parse_entry
from ingestion.paths import asset_relpath, sha256_file

from .conftest import entry, raster_entry, spec, write_raster

pytestmark = pytest.mark.db

ALL_IDS = ("synthetic-worldpop-2001", "synthetic-worldpop-2011", "synthetic-towns")


def _rows(db, dataset_id=None):
    sql = ("SELECT dataset_id, version, title, kind, product_level, year, crs, ST_AsText(bbox) AS bbox, "
           "ST_XMin(bbox) AS w, ST_YMin(bbox) AS s, ST_XMax(bbox) AS e, ST_YMax(bbox) AS n, resolution_x, "
           "resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance, status, created_at, "
           "updated_at FROM catalog.datasets")
    if dataset_id:
        return db.execute(sql + " WHERE dataset_id=%s ORDER BY version", (dataset_id,)).fetchall()
    return db.execute(sql + " ORDER BY dataset_id, version").fetchall()


def _import_all(importer, fixture_dir):
    base, entries = load_manifest(fixture_dir / "manifest.json")
    return [importer.import_dataset(parse_entry(e, base)) for e in entries]


def test_import_fixtures_creates_ready_catalog_rows(importer, db, fresh_fixtures, assets_dir):
    results = _import_all(importer, fresh_fixtures)
    assert [r.outcome for r in results] == ["imported"] * 3
    rows = {r["dataset_id"]: r for r in _rows(db)}
    assert set(rows) == set(ALL_IDS)

    r01 = rows["synthetic-worldpop-2001"]
    assert r01["status"] == "ready" and r01["version"] == 1 and r01["kind"] == "raster"
    assert r01["year"] == 2001 and r01["units"] == "people_per_cell" and float(r01["nodata"]) == -9999
    assert r01["crs"] == "EPSG:4326" and r01["product_level"] == "source"
    assert (r01["w"], r01["s"], r01["e"], r01["n"]) == pytest.approx((78.0, 17.936, 78.064, 18.0))
    assert float(r01["resolution_x"]) == pytest.approx(0.001) and float(r01["resolution_y"]) == pytest.approx(0.001)
    assert r01["asset_path"] == "rasters/synthetic-worldpop-2001/v1/synthetic-worldpop-2001_v1.tif"
    assert r01["checksum_sha256"] == sha256_file(fresh_fixtures / "synthetic_2001.tif")
    assert "SYNTHETIC" in r01["title"] and r01["source"] == "deterministic-fixture"
    prov = r01["provenance"]
    assert prov["synthetic"] is True and prov["raster"]["stats"]["sum"] == 3600
    assert prov["ingestion"]["asset_sha256"] == sha256_file(assets_dir / r01["asset_path"])
    assert prov["ingestion"]["asset"]["format"] == "COG"
    assert rows["synthetic-worldpop-2011"]["provenance"]["raster"]["stats"]["sum"] == 7200

    with rasterio.open(assets_dir / r01["asset_path"]) as cog:
        assert cog.nodata == -9999 and cog.crs.to_epsg() == 4326 and cog.tags()["SYNTHETIC"] == "TRUE"
        arr = cog.read(1)
        assert arr[10:20, 10:20].sum() == 100  # Town A cells
    assert not any((assets_dir / ".staging").iterdir())

    towns = rows["synthetic-towns"]
    assert towns["kind"] == "vector" and towns["year"] is None and towns["nodata"] is None
    assert towns["provenance"]["vector"]["unit_ids"] == ["town-a", "town-b"]
    assert (assets_dir / towns["asset_path"]).is_file()

    current = db.execute("SELECT dataset_id, version FROM catalog.current_datasets ORDER BY 1").fetchall()
    assert [(c["dataset_id"], c["version"]) for c in current] == sorted((i, 1) for i in ALL_IDS)


def test_admin_units_support_spatial_filtering(importer, db, fresh_fixtures):
    _import_all(importer, fresh_fixtures)
    units = db.execute("SELECT unit_id, name, level, GeometryType(geom) AS t, ST_SRID(geom) AS srid, "
                       "ST_IsValid(geom) AS valid, ST_AsText(ST_Envelope(geom)) AS env FROM geo.admin_units "
                       "ORDER BY unit_id").fetchall()
    assert [(u["unit_id"], u["name"], u["level"]) for u in units] == [
        ("town-a", "Town A", "synthetic_town"), ("town-b", "Town B", "synthetic_town")]
    assert all(u["t"] == "MULTIPOLYGON" and u["srid"] == 4326 and u["valid"] for u in units)
    hit = db.execute("SELECT unit_id FROM geo.admin_units WHERE geom && ST_MakeEnvelope(78.0, 17.97, 78.025, "
                     "18.0, 4326)").fetchall()
    assert [h["unit_id"] for h in hit] == ["town-a"]
    cat = db.execute("SELECT dataset_id FROM catalog.current_datasets WHERE bbox && "
                     "ST_MakeEnvelope(78.031, 17.951, 78.032, 17.952, 4326) ORDER BY 1").fetchall()
    assert [c["dataset_id"] for c in cat] == sorted(ALL_IDS)


def test_reimport_is_idempotent(importer, db, fresh_fixtures, assets_dir):
    _import_all(importer, fresh_fixtures)
    before = _rows(db)
    mtimes = {p: p.stat().st_mtime_ns for p in assets_dir.rglob("*") if p.is_file()}
    fixtures.generate(fresh_fixtures)  # regenerate in place: deterministic -> same checksums
    again = _import_all(importer, fresh_fixtures)
    assert [r.outcome for r in again] == ["unchanged"] * 3
    after = _rows(db)
    assert after == before  # includes updated_at: nothing was rewritten
    assert {p: p.stat().st_mtime_ns for p in assets_dir.rglob("*") if p.is_file()} == mtimes
    assert db.execute("SELECT count(*) AS n FROM geo.admin_units").fetchone()["n"] == 2


def test_conflicting_checksum_rejected_without_overwrite(importer, db, fresh_fixtures, assets_dir):
    _import_all(importer, fresh_fixtures)
    rel = str(asset_relpath("raster", "synthetic-worldpop-2001", 1))
    asset_sha = sha256_file(assets_dir / rel)
    before = _rows(db, "synthetic-worldpop-2001")
    with rasterio.open(fresh_fixtures / "synthetic_2001.tif", "r+") as dst:
        a = dst.read(1)
        a[30, 30] = 5
        dst.write(a, 1)
    with pytest.raises(ConflictError) as ei:
        importer.import_dataset(spec(fresh_fixtures, "synthetic-worldpop-2001"))
    assert ei.value.code == "checksum_conflict"
    assert _rows(db, "synthetic-worldpop-2001") == before
    assert sha256_file(assets_dir / rel) == asset_sha


def test_higher_version_becomes_current_and_old_is_kept(importer, db, fresh_fixtures, assets_dir):
    _import_all(importer, fresh_fixtures)
    v2 = write_raster(fresh_fixtures / "v2.tif", np.full((64, 64), 3, np.float32),
                      transform=from_origin(78.0, 18.0, 0.001, 0.001))
    res = importer.import_dataset(spec(fresh_fixtures, "synthetic-worldpop-2001", version=2, file=str(v2),
                                       expect=None))
    assert res.outcome == "imported"
    cur = db.execute("SELECT version, asset_path FROM catalog.current_datasets "
                     "WHERE dataset_id='synthetic-worldpop-2001'").fetchone()
    assert cur["version"] == 2 and cur["asset_path"].endswith("_v2.tif")
    assert [r["status"] for r in _rows(db, "synthetic-worldpop-2001")] == ["ready", "ready"]
    assert (assets_dir / str(asset_relpath("raster", "synthetic-worldpop-2001", 1))).is_file()


def test_failure_after_staging_leaves_no_ready_row_or_asset(importer, db, fresh_fixtures, assets_dir, monkeypatch):
    from ingestion import importer as imp_mod

    def boom(*a, **k):
        raise AssetError("simulated verification failure", code="cog_verification_failed")

    monkeypatch.setattr(imp_mod, "verify_cog", boom)
    s = spec(fresh_fixtures, "synthetic-worldpop-2001")
    with pytest.raises(AssetError):
        importer.import_dataset(s)
    (row,) = _rows(db, s.dataset_id)
    assert row["status"] == "failed"
    assert row["provenance"]["ingestion"]["error"]["code"] == "cog_verification_failed"
    assert db.execute("SELECT count(*) AS n FROM catalog.current_datasets").fetchone()["n"] == 0
    assert not (assets_dir / str(asset_relpath("raster", s.dataset_id, 1))).exists()
    assert not any((assets_dir / ".staging").iterdir())

    monkeypatch.undo()  # retry with the same content succeeds and reuses the failed row
    assert importer.import_dataset(s).outcome == "imported"
    assert [r["status"] for r in _rows(db, s.dataset_id)] == ["ready"]


def test_db_failure_at_publish_rolls_back_asset_and_units(importer, db, fresh_fixtures, assets_dir, monkeypatch):
    s = spec(fresh_fixtures, "synthetic-towns")
    original = importer.catalog._upsert_units

    def upsert_then_fail(spec_, units):
        original(spec_, units)  # units written inside the publish transaction...
        raise RuntimeError("simulated crash before commit")  # ...then rolled back

    monkeypatch.setattr(importer.catalog, "_upsert_units", upsert_then_fail)
    with pytest.raises(RuntimeError):
        importer.import_dataset(s)
    assert db.execute("SELECT count(*) AS n FROM geo.admin_units").fetchone()["n"] == 0
    (row,) = _rows(db, s.dataset_id)
    assert row["status"] == "failed" and row["provenance"]["ingestion"]["error"]["code"] == "internal_error"
    assert not (assets_dir / str(asset_relpath("vector", s.dataset_id, 1))).exists()


def test_existing_different_asset_is_never_overwritten(importer, db, fresh_fixtures, assets_dir):
    s = spec(fresh_fixtures, "synthetic-worldpop-2001")
    final = assets_dir / str(asset_relpath("raster", s.dataset_id, 1))
    final.parent.mkdir(parents=True)
    final.write_bytes(b"someone else's file")
    with pytest.raises(AssetError) as ei:
        importer.import_dataset(s)
    assert ei.value.code == "asset_exists"
    assert final.read_bytes() == b"someone else's file"
    assert _rows(db, s.dataset_id)[0]["status"] == "failed"


def test_identical_leftover_asset_is_adopted(importer, db, fresh_fixtures, assets_dir):
    """Crash after the asset link but before commit: retry adopts the equivalent file."""
    s = spec(fresh_fixtures, "synthetic-worldpop-2001")
    importer.import_dataset(s)
    rel = str(asset_relpath("raster", s.dataset_id, 1))
    keep = (assets_dir / rel).read_bytes()
    db.execute("TRUNCATE catalog.datasets, geo.admin_units")   # integration: admin_units has an FK to datasets
    (assets_dir / rel).write_bytes(keep)
    assert importer.import_dataset(s).outcome == "imported"


def test_validation_failures_do_not_publish(importer, db, fresh_fixtures):
    p = write_raster(fresh_fixtures / "nocrs.tif", np.ones((8, 8), np.float32), crs=None)
    s = parse_entry(raster_entry(p), fresh_fixtures)
    with pytest.raises(ValidationError) as ei:
        importer.import_dataset(s)
    assert ei.value.code == "missing_crs"
    assert db.execute("SELECT count(*) AS n FROM catalog.datasets WHERE status='ready'").fetchone()["n"] == 0


def test_manifest_checksum_mismatch_rejected(importer, db, fresh_fixtures):
    with pytest.raises(AssetError) as ei:
        importer.import_dataset(spec(fresh_fixtures, "synthetic-worldpop-2001", checksum_sha256="0" * 64))
    assert ei.value.code == "checksum_mismatch"
    assert _rows(db) == []


def test_missing_asset_on_reimport_is_reported(importer, db, fresh_fixtures, assets_dir):
    s = spec(fresh_fixtures, "synthetic-worldpop-2011")
    importer.import_dataset(s)
    (assets_dir / str(asset_relpath("raster", s.dataset_id, 1))).unlink()
    with pytest.raises(AssetError) as ei:
        importer.import_dataset(s)
    assert ei.value.code == "asset_missing"


def test_projected_raster_imports_without_synthetic_assumptions(importer, db, tmp_path):
    arr = np.full((40, 30), 12, np.int32)
    arr[0] = -1
    p = write_raster(tmp_path / "utm.tif", arr, crs="EPSG:32644", transform=from_origin(600000, 1990000, 100, 100),
                     nodata=-1)
    s = parse_entry(raster_entry(p, dataset_id="phase1-like-raster", year=2015, product_level="level1",
                                 units="people_per_cell", source="phase1-test"), tmp_path)
    assert importer.import_dataset(s).outcome == "imported"
    (row,) = _rows(db, "phase1-like-raster")
    assert row["crs"] == "EPSG:32644" and float(row["resolution_x"]) == 100 and float(row["nodata"]) == -1
    assert 81 < row["w"] < 82 and row["s"] < row["n"] and row["product_level"] == "level1"
    assert row["provenance"]["raster"]["stats"]["sum"] == 39 * 30 * 12


def test_optional_admin_unit_columns_are_populated(db_conn, importer, db, fresh_fixtures):
    # Integration (Agent 6): Agent 2's schema already has these columns; add/drop them only on the
    # contract stand-in schema, never drop the real ones.
    existing = {r["column_name"] for r in db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='geo' AND table_name='admin_units'")}
    added = not {"dataset_id", "dataset_version", "properties"} <= existing
    if added:
        db.execute("ALTER TABLE geo.admin_units ADD COLUMN dataset_id TEXT, ADD COLUMN dataset_version INTEGER, "
                   "ADD COLUMN properties JSONB")
    try:
        from ingestion.catalog import Catalog
        importer.catalog = Catalog(db)
        importer.import_dataset(spec(fresh_fixtures, "synthetic-towns"))
        row = db.execute("SELECT dataset_id, dataset_version, properties FROM geo.admin_units "
                         "WHERE unit_id='town-a'").fetchone()
        assert row["dataset_id"] == "synthetic-towns" and row["dataset_version"] == 1
        assert row["properties"]["source"] == "deterministic-fixture"
        # a different dataset may not hijack the same unit ids
        other = entry(fresh_fixtures, "synthetic-towns", dataset_id="synthetic-towns-copy")
        with pytest.raises(ConflictError) as ei:
            importer.import_dataset(parse_entry(other, fresh_fixtures))
        assert ei.value.code == "unit_id_conflict"
        assert db.execute("SELECT count(*) AS n FROM geo.admin_units WHERE dataset_id='synthetic-towns'"
                          ).fetchone()["n"] == 2
    finally:
        if added:
            db.execute("ALTER TABLE geo.admin_units DROP COLUMN dataset_id, DROP COLUMN dataset_version, "
                       "DROP COLUMN properties")


def test_concurrent_import_is_locked(importer, db, fresh_fixtures):
    from ingestion.catalog import Catalog, connect

    s = spec(fresh_fixtures, "synthetic-worldpop-2001")
    other = connect(os.environ["INGESTION_TEST_DSN"])
    try:
        with Catalog(other).lock(s):
            with pytest.raises(ConflictError) as ei:
                importer.import_dataset(s)
            assert ei.value.code == "import_in_progress"
    finally:
        other.close()
    assert importer.import_dataset(s).outcome == "imported"


def test_cli_seed_then_import_twice(db, tmp_path, capsys):
    dsn = os.environ["INGESTION_TEST_DSN"]
    fx, assets = tmp_path / "fx", tmp_path / "assets"
    rc = cli.main(["seed", "--fixtures-dir", str(fx), "--assets-dir", str(assets), "--dsn", dsn])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 0 and [x.get("outcome") for x in lines[1:]] == ["imported"] * 3
    rc = cli.main(["import", "--manifest", str(fx / "manifest.json"), "--assets-dir", str(assets), "--dsn", dsn])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 0 and [x["outcome"] for x in lines] == ["unchanged"] * 3


def test_cli_reports_errors_and_exit_codes(db, tmp_path, capsys, fresh_fixtures):
    dsn = os.environ["INGESTION_TEST_DSN"]
    bad = json.loads((fresh_fixtures / "manifest.json").read_text())
    bad["datasets"][0]["kind"] = "pointcloud"
    bad["datasets"][1]["file"] = "missing.tif"
    m = fresh_fixtures / "bad_manifest.json"
    m.write_text(json.dumps(bad))
    rc = cli.main(["import", "--manifest", str(m), "--assets-dir", str(tmp_path / "a"), "--dsn", dsn])
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == cli.EXIT_FAILED
    assert [o["outcome"] for o in out] == ["failed", "failed", "imported"]
    assert out[0]["error"]["code"] == "unsupported_kind" and out[1]["error"]["code"] == "file_not_found"
    assert cli.main(["import", "--manifest", str(tmp_path / "nope.json"), "--dsn", dsn]) == cli.EXIT_USAGE
    assert cli.main(["import", "--manifest", str(m), "--dsn", "host=/nonexistent dbname=x"]) == cli.EXIT_DB


def test_schema_check_rejects_unmigrated_db(db):
    from ingestion.catalog import Catalog

    db.execute("ALTER TABLE catalog.datasets RENAME COLUMN provenance TO prov")
    try:
        with pytest.raises(IngestionError) as ei:
            Catalog(db)
        assert ei.value.code == "schema_incompatible"
    finally:
        db.execute("ALTER TABLE catalog.datasets RENAME COLUMN prov TO provenance")


def test_filesystem_without_hardlinks_falls_back_to_rename(importer, db, fresh_fixtures, assets_dir, monkeypatch):
    import errno

    from ingestion import importer as imp_mod

    def no_link(*a, **k):
        raise OSError(errno.EPERM, "hard links not supported")

    monkeypatch.setattr(imp_mod.os, "link", no_link)
    s = spec(fresh_fixtures, "synthetic-worldpop-2001")
    assert importer.import_dataset(s).outcome == "imported"
    assert (assets_dir / str(asset_relpath("raster", s.dataset_id, 1))).is_file()
    assert importer.import_dataset(s).outcome == "unchanged"
