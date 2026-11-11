# Level 1 vs GHS-POP evaluation on common spatial support and unit (people per cell / per zone).
# Reference: GHS-POP R2023A (3 arc-second, counts). It is itself a *model* (GHSL disaggregation of census
# data with built-up surface) — it is a reference, not ground truth. For years between GHS-POP epochs the
# reference is a linear interpolation of the bracketing epochs (flagged ``reference_temporal=interpolated``).
#
# Supports (all count-preserving aggregations on the WorldPop grid):
#   * s=1   (~100 m native cells): streaming moments + a deterministic systematic sample for Spearman;
#   * s=k   blocks (e.g. 10 = ~1 km, 100 = ~10 km): full in-memory aggregates, exact Spearman;
#   * zones (optional rasterized admin polygons): per-zone totals (state/district conservation + metrics).
# The same metrics are computed for the unmodified WorldPop input as a baseline, so the effect of the
# redistribution itself is visible. NOTE: at supports that are unions of whole parents (s multiple of the
# parent block) Level 1 equals WorldPop by construction (mass conservation), so differences vs the baseline
# can only appear at sub-parent supports (s=1) and across zone boundaries.

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import logging
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from .config import Config
from .grid import ensure_proj_data, grid_from_raster
from .monitor import ResourceMonitor
from . import core
from . import pipeline as P

log = logging.getLogger("cgis_level1.eval")


# Model ``x`` vs reference ``y`` (1-D float64, same support).
# Full metric set (Pearson, log Pearson, Spearman, MAE, RMSE, bias, R2, residual percentiles) for
# in-memory supports and the systematic sample; used by evaluate_year.
def metrics(x: np.ndarray, y: np.ndarray, *, spearman: bool = True) -> dict:
    n = int(x.size)
    if n == 0:
        return {"n": 0}
    d = x - y
    sx, sy = float(x.sum()), float(y.sum())
    out = {"n": n, "sum_model": sx, "sum_reference": sy,
           "mae": float(np.abs(d).mean()), "rmse": float(math.sqrt((d * d).mean())),
           "bias_mean": float(d.mean()), "rel_bias_total": (sx - sy) / sy if sy else None,
           "median_abs_residual": float(np.median(np.abs(d))),
           "p95_abs_residual": float(np.percentile(np.abs(d), 95))}
    if x.std() > 0 and y.std() > 0:
        out["pearson"] = float(np.corrcoef(x, y)[0, 1])
        lx, ly = np.log1p(np.maximum(x, 0)), np.log1p(np.maximum(y, 0))
        out["pearson_log1p"] = float(np.corrcoef(lx, ly)[0, 1])
        ss_res = float((d * d).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        out["r2_vs_reference"] = 1 - ss_res / ss_tot if ss_tot else None
        if spearman:
            from scipy.stats import spearmanr
            out["spearman"] = float(spearmanr(x, y).statistic)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(y > 0, np.abs(d) / y, np.nan)
    if np.isfinite(rel).any():
        out["median_abs_rel_residual_ref_gt0"] = float(np.nanmedian(rel))
    return out


# Metrics from summed moments for the streamed 100 m support (evaluate_year).
def moments_metrics(m: dict) -> dict:
    n = m["n"]
    if not n:
        return {"n": 0}
    mx, my = m["sx"] / n, m["sy"] / n
    vx, vy = m["sxx"] / n - mx * mx, m["syy"] / n - my * my
    cov = m["sxy"] / n - mx * my
    mse = max((m["sxx"] - 2 * m["sxy"] + m["syy"]) / n, 0.0)
    return {"n": n, "sum_model": m["sx"], "sum_reference": m["sy"], "mae": m["sad"] / n,
            "rmse": math.sqrt(mse), "bias_mean": mx - my,
            "rel_bias_total": (m["sx"] - m["sy"]) / m["sy"] if m["sy"] else None,
            "pearson": cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else None,
            "r2_vs_reference": 1 - mse / vy if vy > 0 else None}


# Adds two moment dicts (evaluate_year.consume).
def _addm(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + v for k, v in b.items()}


_ZONES: dict = {}


# Rasterize zone polygons (cell-centre rule => each cell in exactly one zone) for one window.
# Cached per worker in _ZONES; used by eval_tile.
def _zone_raster(cfg: Config, layer: dict, r: int, c: int, h: int, w: int) -> np.ndarray:
    from rasterio.features import rasterize
    key = layer["name"]
    if key not in _ZONES:
        gj = json.loads(cfg.resolve(layer["path"]).read_text(encoding="utf-8"))
        feats = []
        for i, f in enumerate(gj["features"], start=1):
            feats.append((f["geometry"], i, f["properties"].get(layer["id_property"]),
                          f["properties"].get(layer.get("name_property", layer["id_property"]))))
        from shapely.geometry import shape
        bounds = [shape(g).bounds for g, *_ in feats]
        _ZONES[key] = (feats, bounds)
    feats, bounds = _ZONES[key]
    target = P._target(cfg)
    t = target.transform * rasterio.Affine.translation(c, r)
    x0, y1 = t.c, t.f
    x1, y0 = x0 + w * t.a, y1 + h * t.e
    shapes = [(g, i) for (g, i, *_), b in zip(feats, bounds) if not (b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1)]
    if not shapes:
        return np.zeros((h, w), dtype=np.int32)
    return rasterize(shapes, out_shape=(h, w), transform=t, fill=0, dtype="int32", all_touched=False)


# Per-tile evaluation in a worker: moments at 100 m, systematic sample, block aggregates and zone
# totals for Level 1, WorldPop baseline and GHS-POP. Submitted by evaluate_year.
def eval_tile(year: int, r: int, c: int, h: int, w: int) -> dict:
    cfg: Config = P._W["cfg"]
    target = P._target(cfg)
    l1 = P._src(("l1", year), lambda: rasterio.open(cfg.out_dir / "cog" / f"{cfg.dataset_id(year)}.tif"))
    a = l1.read(1, window=Window(c, r, w, h)).astype(np.float64)
    valid = np.isfinite(a) & (a != l1.nodata)
    out: dict = {"r": r, "c": c, "h": h, "w": w}
    ref = P.read_reference(cfg, year, r, c, h, w)
    if ref is None:
        raise RuntimeError(f"no GHS-POP reference available for {year}")
    ref = np.where(np.isnan(ref), 0.0, ref)
    out["ref_total_all_cells"] = float(ref.sum())
    out["ref_outside_l1_mask"] = float(ref[~valid].sum())
    out["ref_cells_outside_l1_mask_gt0"] = int(((ref > 0) & ~valid).sum())
    if not valid.any():
        out["empty"] = True
        return out
    b = P.read_worldpop(cfg, year, r, c, h, w)
    x = np.where(valid, a, 0.0)
    xb = np.where(valid, b, 0.0)
    y = np.where(valid, ref, 0.0)
    out["m1"] = core.moments(x[valid], y[valid])
    out["m1_baseline"] = core.moments(xb[valid], y[valid])
    out["m1_l1_vs_wp"] = core.moments(x[valid], xb[valid])
    # deterministic systematic sample (global cell indices) for s=1 Spearman
    st = cfg.eval_sample_step
    rows = np.arange(r, r + h) + target.row0
    cols = np.arange(c, c + w) + target.col0
    sel = np.zeros((h, w), dtype=bool)
    sel[np.ix_(rows % st == 0, cols % st == 0)] = True
    sel &= valid
    out["sample"] = np.stack([x[sel], xb[sel], y[sel]])
    # block aggregates (tile is a multiple of every support; pad partial edge tiles)
    for s in cfg.eval_supports:
        if s == 1:
            continue
        H, W = math.ceil(h / s) * s, math.ceil(w / s) * s

        def pad(z):
            o = np.zeros((H, W))
            o[:h, :w] = z
            return o
        out[f"s{s}"] = {"l1": core.blocksum(pad(x), s), "wp": core.blocksum(pad(xb), s),
                        "ref": core.blocksum(pad(y), s), "n": core.blocksum(pad(valid.astype(float)), s)}
    for layer in P._W.get("zones", []):
        z = _zone_raster(cfg, layer, r, c, h, w)
        zz = np.where(valid, z, 0).ravel()
        nz = layer["_n"] + 1
        out[f"zone_{layer['name']}"] = {
            "l1": np.bincount(zz, weights=x.ravel(), minlength=nz),
            "wp": np.bincount(zz, weights=xb.ravel(), minlength=nz),
            "ref": np.bincount(zz, weights=y.ravel(), minlength=nz),
            "ref_all": np.bincount(z.ravel(), weights=ref.ravel(), minlength=nz)}
    return out


# Loads the configured zone layers' ids/names (evaluate_year); missing layers are skipped.
def _zone_layers(cfg: Config) -> list[dict]:
    layers = []
    for name, spec in cfg.eval_zones.items():
        p = cfg.resolve(spec["path"])
        if not p.exists():
            log.warning("zone layer %s missing at %s; skipped", name, p)
            continue
        gj = json.loads(p.read_text(encoding="utf-8"))
        ids = [f["properties"].get(spec["id_property"]) for f in gj["features"]]
        names = [f["properties"].get(spec.get("name_property", spec["id_property"])) for f in gj["features"]]
        layers.append({"name": name, **spec, "_n": len(ids), "_ids": ids, "_names": names})
    return layers


# Builds the year's evaluation bundle out_dir/eval/<id>.eval.json (+ figures). Used by cli `evaluate` and tests.
def evaluate_year(cfg: Config, year: int, figures_dir: Path | None = None) -> dict:
    ensure_proj_data()
    cog = cfg.out_dir / "cog" / f"{cfg.dataset_id(year)}.tif"
    if not cog.exists():
        raise FileNotFoundError(cog)
    terms = P.reference_terms(cfg, year)
    if not terms:
        raise RuntimeError(f"no GHS-POP epochs bracket {year}")
    P.prepare_vrts(cfg, [("pop", e) for e, _ in terms])
    target = grid_from_raster(P._first_existing_wp(cfg), cfg.block, cfg.bbox)
    tile = cfg.tile_size
    for s in cfg.eval_supports:
        if tile % s or (cfg.tile_cols % s and cfg.tile_cols < target.width):
            raise ValueError(f"tile_size/tile_cols must be multiples of evaluation support {s}")
    tiles = target.tiles(tile, cfg.tile_cols)
    layers = _zone_layers(cfg)

    acc = {"m1": {}, "m1_baseline": {}, "m1_l1_vs_wp": {}}
    samples = []
    grids = {s: {k: np.zeros((math.ceil(target.height / s), math.ceil(target.width / s)))
                 for k in ("l1", "wp", "ref", "n")} for s in cfg.eval_supports if s != 1}
    zacc = {L["name"]: {k: np.zeros(L["_n"] + 1) for k in ("l1", "wp", "ref", "ref_all")} for L in layers}
    ref_total = ref_out = 0.0
    ref_out_cells = 0

    # Merges one tile result into the national accumulators.
    def consume(d: dict) -> None:
        nonlocal ref_total, ref_out, ref_out_cells
        ref_total += d["ref_total_all_cells"]
        ref_out += d["ref_outside_l1_mask"]
        ref_out_cells += d["ref_cells_outside_l1_mask_gt0"]
        if d.get("empty"):
            return
        for k in acc:
            acc[k] = _addm(acc[k], d[k])
        if d["sample"].shape[1]:
            samples.append(d["sample"])
        for s, g in grids.items():
            blk = d[f"s{s}"]
            r0, c0 = d["r"] // s, d["c"] // s
            for k in g:
                arr = np.asarray(blk[k], dtype=np.float64)
                g[k][r0:r0 + arr.shape[0], c0:c0 + arr.shape[1]] = arr
        for L in layers:
            zd = d[f"zone_{L['name']}"]
            for k in zacc[L["name"]]:
                zacc[L["name"]][k] += np.asarray(zd[k])

    with ResourceMonitor(cfg.max_rss_gb, watch_dir=cfg.out_dir) as mon:
        if cfg.workers == 1:
            P._worker_init(str(cfg.path))
            P._W["zones"] = layers
            for t in tiles:
                consume(eval_tile(year, *t))
        else:
            with cf.ProcessPoolExecutor(max_workers=cfg.workers, initializer=_eval_init,
                                        initargs=(str(cfg.path), layers)) as ex:
                for f in cf.as_completed([ex.submit(eval_tile, year, *t) for t in tiles]):
                    consume(f.result())
                    if mon.exceeded:
                        raise RuntimeError("RSS budget exceeded during evaluation")
        res_mon = mon.report()

    bundle: dict = {
        "dataset_id": cfg.dataset_id(year), "year": year, "synthetic": cfg.synthetic,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "config_sha256": cfg.sha256, "level1_sha256": P.cached_sha256(cog, cfg.work_dir / "sha256_cache.json"),
        "reference": {"product": cfg.provenance_extra.get("reference_product", "GHS-POP R2023A 3 arc-second"),
                      "terms": terms,
                      "reference_temporal": "exact_epoch" if len(terms) == 1 else "interpolated",
                      "caveat": "GHS-POP is a modelled product (census disaggregated with GHSL built-up), "
                                "not ground truth; agreement measures consistency between two models.",
                      "regridding": "exact area-overlap sub-pixel shift of counts onto the WorldPop grid"},
        "units": "people_per_cell (sum over support)",
        "coverage": {"note": "'*_in_extent' terms cover the rectangular processing extent, which includes "
                             "neighbouring countries where WorldPop-India is NoData; use 'within_<zones>' "
                             "for national coverage.",
                     "reference_total_in_extent": ref_total,
                     "reference_outside_level1_valid_mask": ref_out,
                     "reference_cells_with_pop_outside_mask": ref_out_cells,
                     "reference_fraction_outside_mask": ref_out / ref_total if ref_total else None},
        "supports": {},
        "resources": res_mon,
    }
    s1 = {"cells_m": "~100 m native", "level1": moments_metrics(acc["m1"]),
          "worldpop_baseline": moments_metrics(acc["m1_baseline"]),
          "level1_vs_worldpop": moments_metrics(acc["m1_l1_vs_wp"])}
    if samples:
        S = np.concatenate(samples, axis=1)
        s1["sample"] = {"design": f"systematic: every {cfg.eval_sample_step}th row and column of the WorldPop grid",
                        "level1": metrics(S[0], S[2]), "worldpop_baseline": metrics(S[1], S[2])}
    bundle["supports"]["s1"] = s1
    for s, g in grids.items():
        m = g["n"] > 0
        bundle["supports"][f"s{s}"] = {
            "block_cells": s, "approx_size_m": 100 * s, "n_blocks": int(m.sum()),
            "level1": metrics(g["l1"][m], g["ref"][m]),
            "worldpop_baseline": metrics(g["wp"][m], g["ref"][m]),
            "level1_minus_worldpop_max_abs": float(np.abs(g["l1"][m] - g["wp"][m]).max(initial=0)),
        }
    for L in layers:
        z = zacc[L["name"]]
        idx = np.arange(1, L["_n"] + 1)
        has = z["l1"][idx] + z["ref"][idx] > 0
        rows = [{"zone_id": L["_ids"][i - 1], "name": L["_names"][i - 1], "level1": float(z["l1"][i]),
                 "worldpop": float(z["wp"][i]), "ghs_pop": float(z["ref"][i]),
                 "level1_minus_worldpop": float(z["l1"][i] - z["wp"][i])} for i in idx]
        ref_in = float(z["ref_all"][idx].sum())
        ref_in_mask = float(z["ref"][idx].sum())
        bundle["coverage"][f"within_{L['name']}"] = {
            "reference_total_inside_zones": ref_in,
            "reference_inside_zones_and_level1_mask": ref_in_mask,
            "reference_inside_zones_outside_level1_mask": ref_in - ref_in_mask,
            "fraction_outside_mask": (ref_in - ref_in_mask) / ref_in if ref_in else None,
            "level1_total_inside_zones": float(z["l1"][idx].sum()),
            "level1_outside_all_zones": float(z["l1"][0]),
        }
        diff = np.abs(z["l1"][idx] - z["wp"][idx])
        bundle["supports"][f"zone_{L['name']}"] = {
            "source": L.get("citation"), "n_zones": int(L["_n"]),
            "unassigned_level1": float(z["l1"][0]), "unassigned_worldpop": float(z["wp"][0]),
            "conservation_vs_worldpop": {"max_abs_diff": float(diff.max(initial=0)),
                                         "max_rel_diff": float((diff / np.where(z["wp"][idx] > 0, z["wp"][idx], 1)).max(initial=0)),
                                         "note": "zones are not unions of parents; small cross-boundary transfers are expected"},
            "level1": metrics(z["l1"][idx][has], z["ref"][idx][has]),
            "worldpop_baseline": metrics(z["wp"][idx][has], z["ref"][idx][has]),
            "zones": rows,
        }
    if figures_dir is not None:
        try:
            from .figures import year_figures
            bundle["figures"] = year_figures(cfg, year, target, grids, figures_dir)
        except Exception as exc:  # figures must never hide the metrics
            log.exception("figure generation failed")
            bundle["figures_error"] = str(exc)
    out = cfg.out_dir / "eval" / f"{cfg.dataset_id(year)}.eval.json"
    P.atomic_json(out, bundle)
    return bundle


# Worker initialiser for the evaluation pool (config + zone layers).
def _eval_init(cfg_path: str, layers: list[dict]) -> None:
    P._worker_init(cfg_path)
    P._W["zones"] = layers
