# National-scale integrity checks using DECIMATED reads (bounded memory); used by the `national` CLI.
# Footprint = geoBoundaries ADM1 union (diagnostic, claimed extent).
from __future__ import annotations

import json
import resource

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from shapely.geometry import shape
from shapely.ops import unary_union

from .harmonize import valid_mask

# Island groups (lon/lat boxes) that must be inspected explicitly.
ISLANDS = {"andaman_nicobar": (92.0, 6.5, 94.5, 14.0), "lakshadweep": (71.5, 8.0, 74.0, 12.6)}


# Simplified union of ADM1 polygons used as the national footprint by the `national` CLI.
def load_footprint(adm1_geojson: str, simplify_deg: float = 0.005):
    fc = json.load(open(adm1_geojson))
    return unary_union([shape(f["geometry"]) for f in fc["features"]]).simplify(simplify_deg)


# Reads every step-th cell -> (valid mask, values, transform); used by coverage_vs_footprint.
def decimated_presence(path: str, step: int = 16):
    with rasterio.open(path) as ds:
        h, w = ds.height // step, ds.width // step
        a = ds.read(1, out_shape=(h, w), resampling=Resampling.nearest)
        tr = ds.transform * Affine.scale(ds.width / w, ds.height / h)
        return valid_mask(a, ds.nodata), a, tr


# NoData inside / data outside the footprint plus island checks for one WorldPop year; used by the `national` CLI.
def coverage_vs_footprint(path: str, footprint, step: int = 16) -> dict:
    m, a, tr = decimated_presence(path, step)
    inside = ~geometry_mask([footprint], out_shape=m.shape, transform=tr, all_touched=False)
    pos = m & (a > 0)
    rep = {"sample_step": step, "cells_in_footprint": int(inside.sum()),
           "nodata_in_footprint": int((inside & ~m).sum()),
           "nodata_fraction_in_footprint": float((inside & ~m).sum() / max(inside.sum(), 1)),
           "valid_outside_footprint": int((m & ~inside).sum()),
           "populated_outside_footprint": int((pos & ~inside).sum())}
    isl = {}
    for name, (w, s, e, n) in ISLANDS.items():
        r0, c0 = rasterio.transform.rowcol(tr, w, n); r1, c1 = rasterio.transform.rowcol(tr, e, s)
        sub = m[max(r0, 0):r1 + 1, max(c0, 0):c1 + 1]
        subp = pos[max(r0, 0):r1 + 1, max(c0, 0):c1 + 1]
        isl[name] = {"sampled_cells": int(sub.size), "valid": int(sub.sum()), "populated": int(subp.sum())}
    rep["islands"] = isl
    return rep


# Detects WorldPop years with identical checksums; used by the `national` CLI.
def duplicate_year_check(state: dict, years) -> dict:
    shas = {y: state[f"india-worldpop-unconstrained-{y}"].get("sha256") for y in years}
    seen, dup = {}, []
    for y, s in shas.items():
        if s is None:
            continue
        if s in seen:
            dup.append((seen[s], y))
        seen[s] = y
    return {"checksums_recorded": sum(v is not None for v in shas.values()), "duplicate_year_pairs": dup,
            "missing_checksum_years": [y for y, s in shas.items() if s is None]}


# Peak RSS of this process in MB; used by the `national` CLI and sample.build_sample.
def peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


# Opens every downloaded GHSL tile zip and checks CRS, lattice offset and value range; used by the `national` CLI.
def check_ghsl_tiles(state: dict, root, ref_transform, source_ids, step: int = 32) -> dict:
    import zipfile
    from pathlib import Path
    from .harmonize import lattice_offset
    from .ghsl_tiles import tiles_for_bbox_4326, INDIA_BBOX
    expected = tiles_for_bbox_4326(INDIA_BBOX)
    out = {}
    for sid in source_ids:
        rec = state.get(sid, {}).get("tiles", {})
        rows, problems = [], []
        present, absent = set(), set()
        for name, t in rec.items():
            rc = tuple(int(x[1:]) for x in name.rsplit("_V1_0_", 1)[1].split(".")[0].split("_"))
            if t.get("status") == "absent":
                absent.add(rc); continue
            zp = Path(root) / "incoming" / sid / "tiles" / name
            if not zp.exists():
                problems.append(f"{name}: file missing"); continue
            present.add(rc)
            with zipfile.ZipFile(zp) as z:
                tif = next(n for n in z.namelist() if n.lower().endswith(".tif"))
            with rasterio.open(f"zip://{zp}!/{tif}") as ds:
                try:
                    off = lattice_offset(ref_transform, ds.transform, tol=2e-2)
                except ValueError as e:
                    off = None; problems.append(f"{name}: {e}")
                a = ds.read(1, out_shape=(ds.height // step, ds.width // step), resampling=Resampling.nearest)
                m = valid_mask(a, ds.nodata)
                if str(ds.crs) != "EPSG:4326":
                    problems.append(f"{name}: crs {ds.crs}")
                if m.any() and float(a[m].min()) < 0:
                    problems.append(f"{name}: negative values")
                if "built-" in sid and m.any() and float(a[m].max()) > 9300:
                    problems.append(f"{name}: m2/cell max {float(a[m].max())} exceeds plausible cell area")
                rows.append({"tile": name, "w": ds.width, "h": ds.height, "dtype": ds.dtypes[0], "nodata": ds.nodata,
                             "lattice_offset": off})
        missing = sorted(set(expected) - present - absent)
        out[sid] = {"tiles_ok": len(rows), "absent_404": sorted(absent), "not_fetched": missing, "problems": problems}
    return out
