"""End-to-end SYNTHETIC runs: seams, restart/idempotency, QA, manifest contract, evaluation, negatives."""

import json
import shutil

import numpy as np
import pytest
import rasterio

from cgis_level1 import pipeline
from cgis_level1.config import ConfigError, load_config
from cgis_level1.evaluate import evaluate_year

from conftest import write_config


def _read(cfg, year):
    with rasterio.open(cfg.out_dir / "cog" / f"{cfg.dataset_id(year)}.tif") as ds:
        return ds.read(1), ds.profile, ds.units


@pytest.fixture(scope="module")
def base_run(synthetic_inputs, tmp_path_factory):
    out = tmp_path_factory.mktemp("run100")
    cfg = load_config(write_config(out / "c.toml", synthetic_inputs, out, tile=100))
    reps = {y: pipeline.run_year(cfg, y) for y in cfg.years}
    return cfg, reps


def test_all_checks_pass_and_units(base_run):
    cfg, reps = base_run
    for y, r in reps.items():
        assert r["status"] == "ok", r["checks"]
        assert all(r["checks"].values())
        assert r["mass_balance"]["rel_error"] < 1e-6
        arr, prof, units = _read(cfg, y)
        assert prof["nodata"] == -99999 and prof["dtype"] == "float32"
        assert units[0] == "people_per_cell"
        assert prof["crs"].to_epsg() == 4326
    # geotransform identical to the WorldPop input (counts never resampled)
    with rasterio.open(cfg.worldpop_path(2001)) as wp:
        arr, prof, _ = _read(cfg, 2001)
        assert prof["transform"] == wp.transform
        assert (arr == -99999).sum() == (wp.read(1) == wp.nodata).sum()


def test_seam_free_independent_of_tile_size(base_run, synthetic_inputs, tmp_path):
    cfg, _ = base_run
    cfg2 = load_config(write_config(tmp_path / "c.toml", synthetic_inputs, tmp_path, tile=50, workers=2))
    for y in (2001, 2011):
        pipeline.run_year(cfg2, y)
        a, _, _ = _read(cfg, y)
        b, _, _ = _read(cfg2, y)
        assert a.tobytes() == b.tobytes(), f"tile size changed pixels in {y}"


def test_restart_from_checkpoints_is_identical(base_run, synthetic_inputs, tmp_path):
    cfg, _ = base_run
    cfg3 = load_config(write_config(tmp_path / "c.toml", synthetic_inputs, tmp_path, tile=100))
    # simulate an interrupted run: only 2 tiles computed, then crash before assembly
    with pytest.raises(pipeline.RunError):
        pipeline.run_year(cfg3, 2005, max_tiles=2, keep_chunks=True)   # partial extent -> QA refuses PASS
    chunks = sorted((cfg3.work_dir / "chunks" / "2005").glob("*.json"))
    assert len(chunks) == 2
    # corrupt one checkpoint chunk: it must be recomputed, not trusted
    tif = sorted((cfg3.work_dir / "chunks" / "2005").glob("*.tif"))[0]
    tif.write_bytes(b"garbage")
    rep = pipeline.run_year(cfg3, 2005)
    assert rep["status"] == "ok"
    a, _, _ = _read(cfg, 2005)
    b, _, _ = _read(cfg3, 2005)
    assert a.tobytes() == b.tobytes()
    log = [json.loads(l) for l in (cfg3.work_dir / "runlog.jsonl").read_text().splitlines()]
    reuse = [e for e in log if e["event"] == "checkpoints"]
    assert reuse[-1]["reused"] == 1       # one good checkpoint reused, the corrupted one recomputed
    # idempotent re-run: skipped, same checksum
    rep2 = pipeline.run_year(cfg3, 2005)
    assert rep2["output"]["sha256"] == rep["output"]["sha256"]


def test_manifest_matches_ingestion_contract(base_run):
    cfg, _ = base_run
    from ingestion.manifest import load_manifest, parse_entry
    from ingestion.raster import inspect_raster
    base, entries = load_manifest(cfg.out_dir / "manifest.json")
    assert {e["dataset_id"] for e in entries} == {cfg.dataset_id(y) for y in cfg.years}
    for e in entries:
        spec = parse_entry(e, base)
        assert spec.product_level == "level1" and spec.units == "people_per_cell" and spec.is_synthetic
        assert "SYNTHETIC" in spec.title
        inspect_raster(spec)     # the importer's own validation, incl. expect + every block
        prov = e["provenance"]
        for k in ("model_commit", "config_sha256", "method", "upstream", "mass_balance", "grid"):
            assert k in prov
        assert prov["upstream"]["worldpop"]["sha256"]
    e11 = next(e for e in entries if e["year"] == 2011)
    assert "PROVISIONAL" in e11["title"]
    assert e11["provenance"]["method"]["post_last_epoch_policy_approval"] == "pending"


def test_2011_policy_comparison_reported(base_run):
    _, reps = base_run
    cmp = reps[2011]["policy_comparison_2011"]
    assert set(cmp) == {"interpolate_2015", "hold_2010", "extrapolate_linear"}
    for v in cmp.values():
        assert v["sum_out"] == pytest.approx(reps[2011]["mass_balance"]["sum_worldpop_in"], rel=1e-6)
        assert "vs_ref_metrics" in v


def test_missing_2011_policy_refused(synthetic_inputs, tmp_path):
    cfg = load_config(write_config(tmp_path / "c.toml", synthetic_inputs, tmp_path, policy=None))
    with pytest.raises(pipeline.RunError, match="human methodological decision"):
        pipeline.run_year(cfg, 2011)
    assert pipeline.run_year(cfg, 2010)["status"] == "ok"     # other years unaffected


def test_missing_input_refused(synthetic_inputs, tmp_path):
    cfg = load_config(write_config(tmp_path / "c.toml", synthetic_inputs, tmp_path, years=[2001, 2012]))
    with pytest.raises(pipeline.RunError, match="missing"):
        pipeline.run_year(cfg, 2012)


def test_grid_mismatch_between_years_refused(synthetic_inputs, tmp_path):
    inp = tmp_path / "in"
    shutil.copytree(synthetic_inputs, inp)
    p = inp / "worldpop" / "ind_ppp_2005.tif"
    with rasterio.open(p) as ds:
        arr, prof = ds.read(1), ds.profile
    prof["transform"] = prof["transform"] * rasterio.Affine.translation(1, 0)
    with rasterio.open(p, "w", **prof) as ds:
        ds.write(arr, 1)
    cfg = load_config(write_config(tmp_path / "c.toml", inp, tmp_path))
    with pytest.raises(pipeline.RunError, match="grid differs"):
        pipeline.run_year(cfg, 2005)


def test_config_rejects_tile_not_multiple_of_block(synthetic_inputs, tmp_path):
    with pytest.raises(ConfigError, match="multiple"):
        load_config(write_config(tmp_path / "c.toml", synthetic_inputs, tmp_path, tile=105))


def test_negative_worldpop_refused(synthetic_inputs, tmp_path):
    inp = tmp_path / "in"
    shutil.copytree(synthetic_inputs, inp)
    p = inp / "worldpop" / "ind_ppp_2001.tif"
    with rasterio.open(p) as ds:
        arr, prof = ds.read(1), ds.profile
    arr[5, 5] = -3.0
    with rasterio.open(p, "w", **prof) as ds:
        ds.write(arr, 1)
    cfg = load_config(write_config(tmp_path / "c.toml", inp, tmp_path))
    with pytest.raises(pipeline.RunError, match="negative"):
        pipeline.run_year(cfg, 2001)


def test_evaluation_bundle(base_run, tmp_path):
    cfg, _ = base_run
    b = evaluate_year(cfg, 2005, tmp_path / "figs")
    assert b["reference"]["reference_temporal"] == "exact_epoch"
    s1 = b["supports"]["s1"]
    for k in ("pearson", "mae", "rmse", "bias_mean"):
        assert k in s1["level1"]
    assert "spearman" in s1["sample"]["level1"]
    # at unions of whole parents Level 1 equals WorldPop (mass conservation) -> identical metrics
    s10 = b["supports"]["s10"]
    assert s10["level1_minus_worldpop_max_abs"] < 1e-2
    z = b["supports"]["zone_d"]
    assert z["n_zones"] == 2
    total_l1 = sum(r["level1"] for r in z["zones"]) + z["unassigned_level1"]
    assert total_l1 == pytest.approx(s1["level1"]["sum_model"], rel=1e-6)
    assert b["figures"] and all((tmp_path / "figs" / f).exists() for f in b["figures"])
    b3 = evaluate_year(cfg, 2001, None)
    assert b3["reference"]["reference_temporal"] == "interpolated"
    assert b3["reference"]["terms"] == [(2000, pytest.approx(0.8)), (2005, pytest.approx(0.2))] or \
        [tuple(t) for t in b3["reference"]["terms"]] == [(2000, pytest.approx(0.8)), (2005, pytest.approx(0.2))]


def test_inventory_and_evidence(base_run, tmp_path):
    from cgis_level1.evidence import collect
    from cgis_level1.inventory import build_inventory
    cfg, _ = base_run
    inv = build_inventory(cfg, cfg.out_dir / "inputs_inventory.json")
    assert not inv["missing"] and inv["n_files"] > 11
    assert all(len(f["sha256"]) == 64 for f in inv["files"])
    evaluate_year(cfg, 2011, None)
    b = collect(cfg, "t", dest=tmp_path)
    assert len(b["years"]) == len(cfg.years) and all(r["status"] == "ok" for r in b["years"])
    assert (tmp_path / "eval" / "t" / "policy_2011_comparison.json").exists()
    assert (tmp_path / "provenance" / "inputs_inventory.t.json").exists()
    assert b["totals"]["max_peak_rss_gib"] > 0
