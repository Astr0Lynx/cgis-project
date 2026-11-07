"""Independent spot-check of a national Level 1 COG.

Recomputes randomly chosen 10x10 parents from the RAW inputs (WorldPop file + individual GHSL tile files,
located by coordinates, no VRT and no cgis_level1 redistribution code) with a deliberately naive loop
implementation of  p_i = P * R_i / sum R  and compares to the published COG.

    python science/level1/scripts/spot_check.py --year 2003 [--n 300] [--seed 1]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "science" / "level1"))
from cgis_level1.grid import ensure_proj_data  # noqa: E402  (PROJ path only)

ensure_proj_data()
K = 10


def ghsl_value(kind_dir: str, lon: float, lat: float) -> float:
    """Value of the GHSL cell containing (lon, lat), searching the tiles directly."""
    for f in glob.glob(str(ROOT / "data/incoming/ghsl" / kind_dir / "*.tif")):
        with rasterio.open(f) as ds:
            b = ds.bounds
            if b.left <= lon < b.right and b.bottom < lat <= b.top:
                r, c = ds.index(lon, lat)
                return float(ds.read(1, window=Window(c, r, 1, 1))[0, 0])
    return 0.0


def weight(year: int, epochs, lon: float, lat: float, policy: str) -> float:
    def R(e):
        t = ghsl_value(f"GHS_BUILT_S_E{e}_GLOBE_R2023A_4326_3ss", lon, lat)
        n = ghsl_value(f"GHS_BUILT_S_NRES_E{e}_GLOBE_R2023A_4326_3ss", lon, lat)
        return max(t - n, 0.0)
    if year in epochs:
        return R(year)
    if year < 2010:
        e0 = (year // 5) * 5
        w = (year - e0) / 5
        return (1 - w) * R(e0) + w * R(e0 + 5)
    if policy == "interpolate_2015":
        return 0.8 * R(2010) + 0.2 * R(2015)
    if policy == "hold_2010":
        return R(2010)
    return max(1.2 * R(2010) - 0.2 * R(2005), 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--policy", default="interpolate_2015")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    wp = rasterio.open(ROOT / f"data/incoming/worldpop/ind_ppp_{a.year}.tif")
    l1 = rasterio.open(ROOT / f"data/outputs/level1/cog/india-l1-pop-{a.year}.tif")
    # the year's WorldPop file may be a whole-pixel-offset window of the reference (output) grid
    dcf = (l1.transform.c - wp.transform.c) / l1.transform.a
    drf = (wp.transform.f - l1.transform.f) / -l1.transform.e
    dc, dr = round(dcf), round(drf)
    assert abs(dcf - dc) < 1e-3 and abs(drf - dr) < 1e-3, "WorldPop file not on the output lattice"
    rng = random.Random(a.seed)
    checked, worst_cell, worst_parent, skipped = 0, 0.0, 0.0, 0
    fails = []
    while checked < a.n:
        br, bc = rng.randrange(l1.height // K), rng.randrange(l1.width // K)
        win = Window(bc * K, br * K, K, K)
        p = wp.read(1, window=Window(bc * K + dc, br * K + dr, K, K), boundless=True,
                    fill_value=wp.nodata).astype(np.float64)
        valid = p != wp.nodata
        if not valid.any() or p[valid].sum() <= 0:
            skipped += 1
            continue
        P = 0.0
        for i in range(K):
            for j in range(K):
                if valid[i, j]:
                    P += p[i, j]
        Rw = np.zeros((K, K))
        for i in range(K):
            for j in range(K):
                if valid[i, j]:
                    lon, lat = l1.xy(br * K + i, bc * K + j)      # cell centre
                    Rw[i, j] = weight(a.year, (2000, 2005, 2010), lon, lat, a.policy)
        S = float(Rw.sum())
        expect = np.full((K, K), np.nan)
        for i in range(K):
            for j in range(K):
                if valid[i, j]:
                    expect[i, j] = P * Rw[i, j] / S if S > 0 else p[i, j]
        got = l1.read(1, window=win).astype(np.float64)
        got[got == l1.nodata] = np.nan
        same_mask = np.array_equal(np.isnan(got), np.isnan(expect))
        d = np.nanmax(np.abs(got - expect) / np.maximum(np.abs(expect), 1.0))
        pe = abs(np.nansum(got) - P) / max(P, 1.0)
        worst_cell, worst_parent = max(worst_cell, d), max(worst_parent, pe)
        if not same_mask or d > 1e-5 or pe > 1e-5:
            fails.append({"block_row": br, "block_col": bc, "max_rel_cell_diff": d, "parent_rel_err": pe,
                          "mask_equal": same_mask})
        checked += 1
    res = {"year": a.year, "parents_checked": checked, "empty_parents_skipped": skipped, "seed": a.seed,
           "max_rel_cell_diff": worst_cell, "max_parent_rel_err": worst_parent, "failures": fails,
           "tolerance": 1e-5, "pass": not fails,
           "method": "independent naive recomputation from raw WorldPop + GHSL tile files (no VRT, no core)"}
    print(json.dumps(res, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
