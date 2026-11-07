"""Tiled, checkpointed, restartable India-wide Level 1 production.

Per year:
  1. Plan deterministic tiles (multiples of the parent block, anchored at the WorldPop raster origin).
  2. Each tile (in a bounded worker pool): read WorldPop window, GHSL total/NRES epochs, compute R_e, apply
     the temporal rule, redistribute within parents, write a compressed chunk GeoTIFF + JSON checkpoint.
     Existing, checksum-verified checkpoints are skipped on restart.
  3. Assemble chunks (VRT) into one lossless COG, re-read it to verify mass balance/NoData/stats, write
     the run report and the v1 ingestion manifest entry.
Never holds more than ``workers`` tiles in memory; never holds two years at once.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import rasterio
import rasterio.shutil
from affine import Affine
from rasterio.windows import Window

from . import __version__, core
from .config import POLICIES_2011, Config, load_config
from .grid import (TargetGrid, WeightSource, build_mosaic_vrt, ensure_proj_data, grid_from_raster,
                   replace_retry)
from .monitor import ResourceMonitor

log = logging.getLogger("cgis_level1")

COG_OPTIONS = {"COMPRESS": "DEFLATE", "PREDICTOR": "YES", "BLOCKSIZE": "512", "OVERVIEWS": "AUTO",
               "RESAMPLING": "NEAREST", "BIGTIFF": "IF_SAFER", "NUM_THREADS": "ALL_CPUS"}


class RunError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------
def sha256_file(p: Path, bufsize: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def cached_sha256(p: Path, cache_file: Path) -> str:
    """SHA-256 with a (path, size, mtime) cache so large inputs are hashed once."""
    st = p.stat()
    key = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            cache = {}
    if key not in cache:
        cache[key] = sha256_file(p)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
        replace_retry(tmp, cache_file)
    return cache[key]


def git_commit(path: Path) -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, timeout=30
                                  ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    sha = run("rev-parse", "HEAD")
    dirty = bool(run("status", "--porcelain", "--", "."))
    return {"commit": sha or None, "dirty_worktree": dirty}


def atomic_json(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=_json_default))
    replace_retry(tmp, p)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    raise TypeError(type(o))


class RunLog:
    """Append-only JSONL restart log."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def event(self, **kw) -> None:
        kw["ts"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, default=_json_default) + "\n")


# --------------------------------------------------------------------------------------------------
# context shared by main process and workers
# --------------------------------------------------------------------------------------------------
def policies_for_year(cfg: Config, year: int) -> tuple[str | None, list[str]]:
    """Main policy and comparison policies for a year (only years after the last epoch have any)."""
    if year <= cfg.epochs[-1]:
        return None, []
    if cfg.policy_2011 is None:
        raise RunError(f"year {year} is after the last GHSL epoch {cfg.epochs[-1]} and no "
                       "temporal.policy_2011 is configured (human methodological decision required)")
    alts = [p for p in POLICIES_2011 if p != cfg.policy_2011] if cfg.compare_2011_policies else []
    return cfg.policy_2011, alts


def extra_epochs(cfg: Config) -> tuple[int, ...]:
    return tuple(e for e in (2015,) if e > cfg.epochs[-1])


def rules_for_year(cfg: Config, year: int) -> dict[str | None, core.TemporalRule]:
    main, alts = policies_for_year(cfg, year)
    rules = {main: core.temporal_rule(year, cfg.epochs, main, extra_epochs(cfg))}
    for a in alts:
        try:
            rules[a] = core.temporal_rule(year, cfg.epochs, a, extra_epochs(cfg))
        except ValueError as exc:
            log.warning("comparison policy %s unavailable: %s", a, exc)
    return rules


def vrt_path(cfg: Config, kind: str, epoch: int) -> Path:
    return cfg.work_dir / "vrt" / f"ghsl_{kind}_{epoch}.vrt"


def prepare_vrts(cfg: Config, kinds_epochs: list[tuple[str, int]]) -> dict[tuple[str, int], Path]:
    out = {}
    for kind, e in kinds_epochs:
        out[(kind, e)] = build_mosaic_vrt(cfg.ghsl_glob(kind, e), vrt_path(cfg, kind, e))
    return out


_W: dict = {}


def _worker_init(cfg_path: str) -> None:
    ensure_proj_data()
    os.environ.setdefault("GDAL_CACHEMAX", "256")
    _W.clear()
    _W["cfg"] = load_config(cfg_path)
    import atexit
    atexit.register(_close_all)


def _close_all() -> None:
    """Close GDAL handles before interpreter teardown (avoids noisy dealloc errors in workers)."""
    for v in list(_W.values()):
        close = getattr(v, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    _W.clear()


def _src(key: tuple, opener):
    if key not in _W:
        _W[key] = opener()
    return _W[key]


def _target(cfg: Config) -> TargetGrid:
    return _src(("target",), lambda: grid_from_raster(cfg.worldpop_path(cfg.years[0]) if
                                                      cfg.worldpop_path(cfg.years[0]).exists() else
                                                      _first_existing_wp(cfg), cfg.block, cfg.bbox))


def _first_existing_wp(cfg: Config) -> Path:
    for y in cfg.years:
        if cfg.worldpop_path(y).exists():
            return cfg.worldpop_path(y)
    raise RunError("no WorldPop input found for any configured year")


def _weights(cfg: Config, kind: str, epoch: int) -> WeightSource:
    return _src(("w", kind, epoch), lambda: WeightSource(vrt_path(cfg, kind, epoch), _target(cfg), cfg.ghsl_nodata))


def read_reference(cfg: Config, year: int, r: int, c: int, h: int, w: int) -> np.ndarray | None:
    """GHS-POP reference counts on the target window: exact epoch, or linear interpolation of epochs."""
    eps = cfg.ghs_pop_epochs
    if not eps or cfg.ghs_pop_glob is None:
        return None
    if year in eps:
        terms = [(year, 1.0)]
    elif eps[0] < year < eps[-1]:
        e0 = max(e for e in eps if e < year)
        e1 = min(e for e in eps if e > year)
        wgt = (year - e0) / (e1 - e0)
        terms = [(e0, 1 - wgt), (e1, wgt)]
    else:
        return None
    out = None
    for e, coef in terms:
        v, _ = _weights(cfg, "pop", e).read(r, c, h, w)
        out = coef * v if out is None else out + coef * v
    return out


def reference_terms(cfg: Config, year: int) -> list[tuple[int, float]] | None:
    eps = cfg.ghs_pop_epochs
    if year in eps:
        return [(year, 1.0)]
    if eps and eps[0] < year < eps[-1]:
        e0 = max(e for e in eps if e < year)
        e1 = min(e for e in eps if e > year)
        wgt = (year - e0) / (e1 - e0)
        return [(e0, 1 - wgt), (e1, wgt)]
    return None


# --------------------------------------------------------------------------------------------------
# tile worker
# --------------------------------------------------------------------------------------------------
def _pad(a: np.ndarray, k: int, fill) -> np.ndarray:
    h, w = a.shape
    H, W = math.ceil(h / k) * k, math.ceil(w / k) * k
    if (H, W) == (h, w):
        return a
    out = np.full((H, W), fill, dtype=a.dtype)
    out[:h, :w] = a
    return out


def chunk_paths(cfg: Config, year: int, r: int, c: int) -> tuple[Path, Path]:
    d = cfg.work_dir / "chunks" / str(year)
    stem = f"r{r:06d}_c{c:06d}"
    return d / f"{stem}.tif", d / f"{stem}.json"


def process_tile(year: int, r: int, c: int, h: int, w: int) -> dict:
    cfg: Config = _W["cfg"]
    t0 = time.perf_counter()
    target = _target(cfg)
    tif, js = chunk_paths(cfg, year, r, c)
    wp = _src(("wp", year), lambda: rasterio.open(cfg.worldpop_path(year)))
    win = Window(target.col0 + c, target.row0 + r, w, h)
    pop = wp.read(1, window=win).astype(np.float64)
    nod = wp.nodata
    valid = np.isfinite(pop)
    if nod is not None:
        valid &= pop != nod
    neg_in = int((pop[valid] < 0).sum())
    res: dict = {"year": year, "r": r, "c": c, "h": h, "w": w, "empty": not bool(valid.any())}
    if res["empty"]:
        res["elapsed_s"] = round(time.perf_counter() - t0, 3)
        atomic_json(js, res)
        return res
    if neg_in:
        raise RunError(f"{year} tile r{r} c{c}: {neg_in} negative WorldPop counts")

    rules = rules_for_year(cfg, year)
    needed = sorted({e for rule in rules.values() for e in rule.epochs})
    r_by_epoch: dict[int, np.ndarray] = {}
    rstats: dict[int, dict] = {}
    n_outside = 0
    for e in needed:
        tot, n_out = _weights(cfg, "total", e).read(r, c, h, w)
        nres, _ = _weights(cfg, "nres", e).read(r, c, h, w)
        re_, st = core.residential(tot, nres)
        # only count diagnostics where WorldPop is valid (the eligible support)
        st_v = core.ResidentialStats(cells=int(valid.sum()),
                                     nres_gt_total=int(((nres > tot) & valid).sum()),
                                     nres_gt_total_m2=float(np.where((nres > tot) & valid, nres - tot, 0).sum()),
                                     missing=int((np.isnan(re_) & valid).sum()))
        r_by_epoch[e] = re_
        rstats[e] = st_v.__dict__
        n_outside = max(n_outside, n_out)
    k = cfg.block
    popP, validP = _pad(pop, k, 0.0), _pad(valid, k, False)
    outs = {}
    for pol, rule in rules.items():
        wgt = core.apply_rule(rule, r_by_epoch)
        o, st = core.redistribute_blocks(popP, validP, _pad(wgt, k, 0.0), k, cfg.zero_weight_fallback)
        outs[pol] = (o[:h, :w], st, float(np.nansum(np.where(valid, wgt, 0))))
    main_pol = next(iter(rules))
    out, st, wsum = outs[main_pol]
    res.update({"redist": st.__dict__, "residential": {str(e): v for e, v in rstats.items()},
                "weight_sum_m2": wsum, "ghsl_outside_cells": n_outside,
                "policy": main_pol, "rule": rules[main_pol].description})

    # comparison of alternative 2011 policies (difference + agreement with reference)
    if len(outs) > 1:
        ref = read_reference(cfg, year, r, c, h, w)
        cmp = {}
        for pol, (o, st2, ws2) in outs.items():
            ov = np.where(valid, o.astype(np.float64), 0.0)
            d = {"weight_sum_m2": ws2, "sum_out": st2.sum_out, "zero_weight_parents": st2.parents_zero_weight}
            if pol != main_pol:
                diff = ov - np.where(valid, out.astype(np.float64), 0.0)
                d.update({"sum_abs_diff_vs_main": float(np.abs(diff).sum()),
                          "max_abs_diff_vs_main": float(np.abs(diff).max(initial=0)),
                          "cells_diff_gt_0p5": int((np.abs(diff) > 0.5).sum())})
            if ref is not None:
                m = valid
                x, y = ov[m], ref[m]
                y = np.where(np.isnan(y), 0.0, y)
                d["vs_ref"] = {"n": int(m.sum()), "sx": float(x.sum()), "sy": float(y.sum()),
                               "sxx": float((x * x).sum()), "syy": float((y * y).sum()),
                               "sxy": float((x * y).sum()), "sad": float(np.abs(x - y).sum())}
            cmp[str(pol)] = d
        res["policy_comparison"] = cmp

    tif.parent.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "height": h, "width": w, "count": 1, "dtype": "float32",
            "crs": wp.crs, "transform": target.transform * Affine.translation(c, r),
            "nodata": cfg.output_nodata, "tiled": True, "blockxsize": 256, "blockysize": 256,
            "compress": "deflate", "predictor": 3}
    data = np.where(valid, out, cfg.output_nodata).astype(np.float32)
    tmp = tif.with_name(tif.stem + ".part.tif")
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(data, 1)
        dst.update_tags(1, UNITS="people_per_cell")
    replace_retry(tmp, tif)
    res["chunk_sha256"] = sha256_file(tif)
    res["elapsed_s"] = round(time.perf_counter() - t0, 3)
    atomic_json(js, res)
    return res


def checkpoint_ok(cfg: Config, year: int, r: int, c: int) -> dict | None:
    tif, js = chunk_paths(cfg, year, r, c)
    if not js.exists():
        return None
    try:
        d = json.loads(js.read_text())
    except json.JSONDecodeError:
        return None
    if d.get("empty"):
        return d
    if tif.exists() and d.get("chunk_sha256") == sha256_file(tif):
        return d
    return None


# --------------------------------------------------------------------------------------------------
# assembly, verification, manifests
# --------------------------------------------------------------------------------------------------
def write_chunk_vrt(cfg: Config, target: TargetGrid, tiles: list[dict], path: Path) -> Path:
    t = target.transform
    parts = [f'<VRTDataset rasterXSize="{target.width}" rasterYSize="{target.height}">',
             f"  <SRS>{escape(target.crs_wkt)}</SRS>",
             f"  <GeoTransform>{t.c!r}, {t.a!r}, 0.0, {t.f!r}, 0.0, {t.e!r}</GeoTransform>",
             '  <VRTRasterBand dataType="Float32" band="1">',
             f"    <NoDataValue>{cfg.output_nodata!r}</NoDataValue>",
             "    <UnitType>people_per_cell</UnitType>"]
    for d in tiles:
        if d.get("empty"):
            continue
        tif, _ = chunk_paths(cfg, d["year"], d["r"], d["c"])
        parts.append(f'    <ComplexSource><SourceFilename relativeToVRT="0">{escape(str(tif))}</SourceFilename>'
                     f'<SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="{d["w"]}" ySize="{d["h"]}"/>'
                     f'<DstRect xOff="{d["c"]}" yOff="{d["r"]}" xSize="{d["w"]}" ySize="{d["h"]}"/>'
                     f'<NODATA>{cfg.output_nodata!r}</NODATA></ComplexSource>')
    parts += ["  </VRTRasterBand>", "</VRTDataset>"]
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def raster_stats(path: Path) -> dict:
    """Stream every block: valid count, sum, min, max, negatives, NaN/Inf."""
    n = 0
    s = 0.0
    mn, mx = math.inf, -math.inf
    neg = nonfinite = 0
    with rasterio.open(path) as ds:
        nod = ds.nodata
        for _, win in ds.block_windows(1):
            a = ds.read(1, window=win).astype(np.float64)
            nonfinite += int((~np.isfinite(a)).sum())
            v = a[np.isfinite(a) & (a != nod)] if nod is not None else a[np.isfinite(a)]
            if v.size:
                n += v.size
                s += float(v.sum())
                mn = min(mn, float(v.min()))
                mx = max(mx, float(v.max()))
                neg += int((v < 0).sum())
        meta = {"crs": ds.crs.to_string(), "epsg": ds.crs.to_epsg(), "transform": list(ds.transform)[:6],
                "width": ds.width, "height": ds.height, "dtype": ds.dtypes[0], "nodata": nod,
                "bounds": list(ds.bounds), "res": list(ds.res), "units": ds.units[0] if ds.units else None,
                "overviews": ds.overviews(1), "compression": str(ds.compression.value if ds.compression else None)}
    return {"valid_cells": n, "sum": s, "min": mn if n else None, "max": mx if n else None,
            "negative_cells": neg, "nonfinite_cells": nonfinite, **meta}


def validate_cog(path: Path) -> dict:
    try:
        from rio_cogeo.cogeo import cog_validate
    except ImportError:  # pragma: no cover
        return {"validated": False, "reason": "rio-cogeo not installed"}
    ok, errors, warnings = cog_validate(str(path), quiet=True)
    return {"validated": True, "is_cog": bool(ok), "errors": errors, "warnings": warnings}


def upstream_provenance(cfg: Config, year: int, rules: dict) -> dict:
    cache = cfg.work_dir / "sha256_cache.json"
    wp = cfg.worldpop_path(year)
    epochs = sorted({e for rule in rules.values() for e in rule.epochs})
    ghsl = {}
    for kind in ("total", "nres"):
        for e in epochs:
            import glob as _g
            files = sorted(_g.glob(cfg.ghsl_glob(kind, e)))
            ghsl[f"{kind}_{e}"] = {Path(f).name: cached_sha256(Path(f), cache) for f in files}
    up = cfg.provenance_extra.get("upstream", {})
    return {
        "worldpop": {"dataset_id": f"india-worldpop-unconstrained-{year}", "file": wp.name,
                     "sha256": cached_sha256(wp, cache), **up.get("worldpop", {})},
        "ghsl_built_s": {"tiles_sha256": ghsl, **up.get("ghsl_built_s", {})},
    }


def build_manifest_entry(cfg: Config, year: int, cog: Path, stats: dict, report: dict, rules: dict) -> dict:
    main_rule = next(iter(rules.values()))
    synthetic = cfg.synthetic
    title = (f"{'SYNTHETIC ' if synthetic else ''}India Level 1 population {year} "
             f"(WorldPop redistributed by GHSL residential built-up surface)")
    if year > cfg.epochs[-1] and not cfg.policy_2011_approval.startswith("approved"):
        title += " [PROVISIONAL: post-2010 policy awaiting human sign-off]"
    return {
        "dataset_id": cfg.dataset_id(year),
        "version": cfg.dataset_version,
        "title": title,
        "description": ("Level 1 dasymetric redistribution: WorldPop unconstrained counts aggregated to "
                        f"{cfg.block}x{cfg.block}-cell parents and redistributed to ~100 m cells in proportion "
                        "to GHSL residential built-up surface (total - NRES). " + main_rule.description + "."),
        "kind": "raster",
        "product_level": "level1",
        "year": year,
        "units": "people_per_cell",
        "source": ("deterministic-fixture (SYNTHETIC cgis_level1 run)" if synthetic else
                   "CyberGIS Level 1 model (cgis_level1) from WorldPop + GHSL GHS-BUILT-S R2023A"),
        "file": os.path.relpath(cog, cfg.out_dir).replace(os.sep, "/"),
        "band": 1,
        "checksum_sha256": report["output"]["sha256"],
        "expect": {"crs": f"EPSG:{stats['epsg']}", "nodata": cfg.output_nodata, "dtype": "float32",
                   "width": stats["width"], "height": stats["height"], "band_count": 1,
                   "resolution": [stats["res"][0], stats["res"][1]], "bounds": stats["bounds"]},
        "provenance": {
            "synthetic": synthetic,
            "model": "cgis_level1", "model_version": __version__,
            "model_commit": report["software"]["commit"], "dirty_worktree": report["software"]["dirty_worktree"],
            "config_file": cfg.path.name, "config_sha256": cfg.sha256,
            "method": {
                "residential_proxy": "R_e = max(GHS_BUILT_S_total_e - GHS_BUILT_S_NRES_e, 0) [m2 surface/cell]",
                "temporal_rule": {"kind": main_rule.kind, "terms": main_rule.terms,
                                  "description": main_rule.description},
                "post_last_epoch_policy": cfg.policy_2011 if year > cfg.epochs[-1] else None,
                "post_last_epoch_policy_approval": cfg.policy_2011_approval if year > cfg.epochs[-1] else None,
                "redistribution": "p_ai = P_a * R_ai / sum_j R_aj",
                "parent_support": f"{cfg.block}x{cfg.block} WorldPop cells anchored at WorldPop raster origin",
                "zero_weight_fallback": cfg.zero_weight_fallback,
                "weight_regridding": "exact area-overlap sub-pixel shift (same 3 arc-second cell size)",
                "counts_resampled": False,
            },
            "grid": {"crs": stats["crs"], "transform": stats["transform"], "nodata": cfg.output_nodata,
                     "pixel": "WorldPop native ~3 arc-second (~100 m) grid",
                     "extent_is_full_worldpop_grid": report["grid"]["extent_is_full_worldpop_grid"]},
            "upstream": report["upstream"],
            "mass_balance": report["mass_balance"],
            "generated_at": report["finished_at"],
            "licenses": cfg.provenance_extra.get("licenses", {}),
            "evaluation_bundle": f"eval/{cfg.dataset_id(year)}.eval.json",
        },
    }


def write_manifest(cfg: Config) -> Path:
    entries = []
    for y in cfg.years:
        p = cfg.out_dir / "manifests" / f"{cfg.dataset_id(y)}.json"
        if p.exists():
            entries.append(json.loads(p.read_text()))
    m = {"manifest_version": 1, "datasets": entries}
    out = cfg.out_dir / "manifest.json"
    atomic_json(out, m)
    return out


# --------------------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------------------
def run_year(cfg: Config, year: int, *, force: bool = False, keep_chunks: bool = False,
             max_tiles: int | None = None) -> dict:
    ensure_proj_data()
    cog = cfg.out_dir / "cog" / f"{cfg.dataset_id(year)}.tif"
    report_path = cfg.out_dir / "reports" / f"{cfg.dataset_id(year)}.run.json"
    runlog = RunLog(cfg.work_dir / "runlog.jsonl")
    if not force and cog.exists() and report_path.exists():
        rep = json.loads(report_path.read_text())
        if rep.get("status") == "ok" and rep.get("config_sha256") == cfg.sha256 and \
                rep["output"]["sha256"] == cached_sha256(cog, cfg.work_dir / "sha256_cache.json"):
            log.info("%s: up-to-date output exists, skipping (use --force to recompute)", year)
            runlog.event(event="year_skip_uptodate", year=year)
            return rep
    wp_path = cfg.worldpop_path(year)
    if not wp_path.exists():
        raise RunError(f"WorldPop input for {year} missing: {wp_path}")
    rules = rules_for_year(cfg, year)
    epochs = sorted({e for rule in rules.values() for e in rule.epochs})
    prepare_vrts(cfg, [(k, e) for k in ("total", "nres") for e in epochs])
    if len(rules) > 1 and cfg.ghs_pop_glob:
        terms = reference_terms(cfg, year)
        if terms:
            prepare_vrts(cfg, [("pop", e) for e, _ in terms])

    target = grid_from_raster(wp_path, cfg.block, cfg.bbox)
    ref_target = grid_from_raster(_first_existing_wp(cfg), cfg.block, cfg.bbox)
    if target.signature() != ref_target.signature() or (target.row0, target.col0, target.height, target.width) != \
            (ref_target.row0, ref_target.col0, ref_target.height, ref_target.width):
        raise RunError(f"WorldPop {year} grid differs from the reference year grid: {target.signature()} vs "
                       f"{ref_target.signature()}")
    tiles = target.tiles(cfg.tile_size, cfg.tile_cols)
    if max_tiles is not None:
        tiles = tiles[:max_tiles]
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    sw = git_commit(Path(__file__).resolve().parent)   # recorded at START: the code that actually runs
    runlog.event(event="year_start", year=year, tiles=len(tiles), policy=next(iter(rules)), force=force)
    if force:
        shutil.rmtree(cfg.work_dir / "chunks" / str(year), ignore_errors=True)

    results: list[dict] = []
    todo = []
    for (r, c, h, w) in tiles:
        d = checkpoint_ok(cfg, year, r, c)
        if d is not None:
            results.append(d)
        else:
            todo.append((r, c, h, w))
    runlog.event(event="checkpoints", year=year, reused=len(results), todo=len(todo))
    log.info("%s: %d tiles (%d reused from checkpoints, %d to compute), %d workers",
             year, len(tiles), len(results), len(todo), cfg.workers)

    with ResourceMonitor(cfg.max_rss_gb, watch_dir=cfg.work_dir) as mon:
        if cfg.workers == 1:
            _worker_init(str(cfg.path))
            for i, t in enumerate(todo):
                results.append(process_tile(year, *t))
                if mon.exceeded:
                    raise RunError(f"RSS budget {cfg.max_rss_gb} GiB exceeded")
                if (i + 1) % 25 == 0:
                    log.info("%s: %d/%d tiles", year, i + 1, len(todo))
        else:
            with cf.ProcessPoolExecutor(max_workers=cfg.workers, initializer=_worker_init,
                                        initargs=(str(cfg.path),)) as ex:
                futs = {ex.submit(process_tile, year, *t): t for t in todo}
                done = 0
                for f in cf.as_completed(futs):
                    results.append(f.result())
                    done += 1
                    if mon.exceeded:
                        for g in futs:
                            g.cancel()
                        raise RunError(f"RSS budget {cfg.max_rss_gb} GiB exceeded")
                    if done % 25 == 0:
                        log.info("%s: %d/%d tiles", year, done, len(todo))
                        runlog.event(event="progress", year=year, done=done, todo=len(todo))
        compute_res = mon.report()

    results.sort(key=lambda d: (d["r"], d["c"]))
    agg = core.RedistStats()
    rs: dict[str, core.ResidentialStats] = {}
    cmp_agg: dict[str, dict] = {}
    outside = 0
    tile_time = 0.0
    for d in results:
        tile_time += d.get("elapsed_s", 0.0)
        if d.get("empty"):
            continue
        agg.add(core.RedistStats(**d["redist"]))
        outside += d.get("ghsl_outside_cells", 0)
        for e, v in d["residential"].items():
            rs.setdefault(e, core.ResidentialStats()).add(core.ResidentialStats(**v))
        for pol, v in d.get("policy_comparison", {}).items():
            a = cmp_agg.setdefault(pol, {})
            for kk, vv in v.items():
                if isinstance(vv, dict):
                    sub = a.setdefault(kk, {})
                    for k2, v2 in vv.items():
                        sub[k2] = sub.get(k2, 0) + v2
                elif kk.startswith("max_"):
                    a[kk] = max(a.get(kk, 0), vv)
                else:
                    a[kk] = a.get(kk, 0) + vv
    for pol, a in cmp_agg.items():
        if "vs_ref" in a:
            a["vs_ref_metrics"] = _moments_to_metrics(a.pop("vs_ref"))

    # assemble COG
    with ResourceMonitor(cfg.max_rss_gb, watch_dir=cfg.out_dir.parent) as mon2:
        cog.parent.mkdir(parents=True, exist_ok=True)
        vrt = write_chunk_vrt(cfg, target, results, cfg.work_dir / f"assemble_{year}.vrt")
        tmp_cog = cog.with_name(cog.stem + ".part.tif")
        with rasterio.Env(GDAL_CACHEMAX=512):
            rasterio.shutil.copy(str(vrt), str(tmp_cog), driver="COG", **COG_OPTIONS)
        replace_retry(tmp_cog, cog)   # band unit comes from the VRT <UnitType>; never update a COG in place
        stats = raster_stats(cog)
        cogv = validate_cog(cog)
        cog_sha = sha256_file(cog)
        assemble_res = mon2.report()

    # QA / mass balance
    abs_err = abs(stats["sum"] - agg.sum_in)
    rel_err = abs_err / agg.sum_in if agg.sum_in else 0.0
    chunk_vs_cog = abs(stats["sum"] - agg.sum_out)
    checks = {
        "national_mass_balance": rel_err <= cfg.mass_rel_tol,
        "parent_mass_balance": agg.max_parent_rel_err <= cfg.mass_rel_tol or
                               agg.max_parent_abs_err <= cfg.mass_abs_tol,
        "chunks_equal_cog": chunk_vs_cog <= max(cfg.mass_abs_tol, 1e-9 * abs(agg.sum_out)),
        "valid_cells_preserved": stats["valid_cells"] == agg.valid_cells,
        "no_negative": stats["negative_cells"] == 0 and agg.negative_out == 0,
        "no_nonfinite": stats["nonfinite_cells"] == 0,
        "is_cog": bool(cogv.get("is_cog", False)),
        "rss_within_budget": not (compute_res["budget_exceeded"] or assemble_res["budget_exceeded"]),
        "all_planned_tiles_processed": max_tiles is None,
    }
    status = "ok" if all(checks.values()) else "failed"
    report = {
        "status": status, "year": year, "dataset_id": cfg.dataset_id(year), "synthetic": cfg.synthetic,
        "config": str(cfg.path), "config_sha256": cfg.sha256, "started_at": started,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "software": {**sw, "cgis_level1": __version__, "rasterio": rasterio.__version__,
                     "gdal": rasterio.__gdal_version__, "numpy": np.__version__},
        "grid": {"full": target.signature(), "extent_row0": target.row0, "extent_col0": target.col0,
                 "extent_height": target.height, "extent_width": target.width,
                 "tile_size": cfg.tile_size, "tile_cols": cfg.tile_cols, "block": cfg.block, "tiles": len(tiles),
                 "nonempty_tiles": sum(not d.get("empty") for d in results),
                 "extent_is_full_worldpop_grid": (target.row0, target.col0, target.height, target.width) ==
                                                 (0, 0, target.full_height, target.full_width)},
        "temporal": {str(p): {"kind": r.kind, "terms": r.terms, "description": r.description}
                     for p, r in rules.items()},
        "main_policy": next(iter(rules)),
        "policy_2011_approval": cfg.policy_2011_approval if year > cfg.epochs[-1] else None,
        "redistribution": agg.__dict__,
        "residential": {e: v.__dict__ for e, v in sorted(rs.items())},
        "ghsl_outside_footprint_cells": outside,
        "mass_balance": {"sum_worldpop_in": agg.sum_in, "sum_level1_out": stats["sum"],
                         "abs_error": abs_err, "rel_error": rel_err,
                         "max_parent_abs_error": agg.max_parent_abs_err,
                         "max_parent_rel_error": agg.max_parent_rel_err,
                         "tolerance": {"rel": cfg.mass_rel_tol, "abs": cfg.mass_abs_tol},
                         "parents": agg.parents, "parents_zero_weight": agg.parents_zero_weight,
                         "population_in_zero_weight_parents": agg.pop_zero_weight},
        "policy_comparison_2011": cmp_agg or None,
        "output": {"path": str(cog), "sha256": cog_sha, "size_bytes": cog.stat().st_size,
                   "stats": stats, "cog_validation": cogv},
        "resources": {"compute": compute_res, "assemble": assemble_res, "sum_tile_cpu_s": round(tile_time, 1),
                      "workers": cfg.workers, "chunk_bytes": sum(
                          chunk_paths(cfg, year, d["r"], d["c"])[0].stat().st_size for d in results
                          if not d.get("empty") and chunk_paths(cfg, year, d["r"], d["c"])[0].exists())},
        "checks": checks,
    }
    report["upstream"] = upstream_provenance(cfg, year, rules)
    atomic_json(report_path, report)
    entry = build_manifest_entry(cfg, year, cog, stats, report, rules)
    atomic_json(cfg.out_dir / "manifests" / f"{cfg.dataset_id(year)}.json", entry)
    write_manifest(cfg)
    runlog.event(event="year_done", year=year, status=status, sha256=cog_sha, checks=checks)
    if status == "ok" and not keep_chunks:
        shutil.rmtree(cfg.work_dir / "chunks" / str(year), ignore_errors=True)
        vrt.unlink(missing_ok=True)
    if status != "ok":
        raise RunError(f"{year}: QA checks failed: {[k for k, v in checks.items() if not v]}")
    return report


def _moments_to_metrics(m: dict) -> dict:
    n = m["n"]
    if not n:
        return {}
    mx, my = m["sx"] / n, m["sy"] / n
    vx = m["sxx"] / n - mx * mx
    vy = m["syy"] / n - my * my
    cov = m["sxy"] / n - mx * my
    mse = (m["sxx"] - 2 * m["sxy"] + m["syy"]) / n
    return {"n": n, "pearson": cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else None,
            "mae": m["sad"] / n, "rmse": math.sqrt(max(mse, 0.0)), "bias": mx - my,
            "sum_model": m["sx"], "sum_reference": m["sy"]}
