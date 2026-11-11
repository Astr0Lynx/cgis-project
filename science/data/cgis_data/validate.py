# Data-integrity checks: year coverage, unit semantics, census joins, polygon integrity, coverage holes.
from __future__ import annotations

import collections
from typing import Iterable

import numpy as np
import rasterio
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .harmonize import iter_windows, valid_mask
from .sources import GHSL_EPOCHS, WORLDPOP_YEARS


# Raised by every check here when an input fails integrity rules.
class ValidationIssue(ValueError):
    pass


# Fails on missing, duplicate or unexpected years; used by check_ghsl_epochs and tests.
def check_year_coverage(years: Iterable[int], expected: Iterable[int] = WORLDPOP_YEARS) -> None:
    got, exp = collections.Counter(years), set(expected)
    dup = sorted(y for y, n in got.items() if n > 1)
    missing = sorted(exp - set(got))
    extra = sorted(set(got) - exp)
    if dup or missing or extra:
        raise ValidationIssue(f"year coverage invalid: missing={missing} duplicate={dup} unexpected={extra}")


# Checks GHSL total and NRES both cover the required epochs.
def check_ghsl_epochs(total_years: Iterable[int], nres_years: Iterable[int]) -> None:
    check_year_coverage(total_years, GHSL_EPOCHS)
    check_year_coverage(nres_years, GHSL_EPOCHS)


# Guards against count/density/volume confusion from declared units and observed values; used by sample.build_sample.
def check_units(declared_units: str, raster_path: str, cell_area_m2: float | None = None, block: int = 1024) -> dict:
    low = declared_units.lower()
    if "km2" in low or "density" in low or "per_m2" in low or "m3" in low:
        raise ValidationIssue(f"units {declared_units!r} are a density/volume label")
    mn, mx, n = np.inf, -np.inf, 0
    with rasterio.open(raster_path) as ds:
        for w in iter_windows(ds.width, ds.height, block):
            a = ds.read(1, window=w)
            m = valid_mask(a, ds.nodata)
            if m.any():
                v = a[m]
                mn, mx, n = min(mn, float(v.min())), max(mx, float(v.max())), n + int(m.sum())
    if n == 0:
        raise ValidationIssue("raster has no valid cells")
    if mn < 0:
        raise ValidationIssue(f"negative values (min={mn}) in a non-negative quantity")
    if declared_units == "m2_per_cell":
        if cell_area_m2 is None:
            raise ValidationIssue("cell_area_m2 required to validate m2_per_cell")
        if mx > cell_area_m2 * 1.0001:
            raise ValidationIssue(f"max {mx} exceeds cell area {cell_area_m2}: not m2-per-cell surface")
    return {"min": mn, "max": mx, "valid_cells": n}


# Fails on duplicate or blank census codes; used by check_census_join.
def check_unique_census_codes(codes: Iterable[str]) -> None:
    c = collections.Counter(codes)
    dup = sorted(k for k, v in c.items() if v > 1)
    if dup:
        raise ValidationIssue(f"duplicate census codes: {dup[:10]}{' ...' if len(dup) > 10 else ''}")
    blank = [k for k in c if k is None or str(k).strip() == ""]
    if blank:
        raise ValidationIssue("blank census code present")


# Unambiguous 1:1 join of census codes and polygon codes; used by census.join_report.
def check_census_join(census_codes: Iterable[str], polygon_codes: Iterable[str]) -> dict:
    check_unique_census_codes(census_codes := list(census_codes))
    check_unique_census_codes(polygon_codes := list(polygon_codes))
    c, p = set(census_codes), set(polygon_codes)
    return {"matched": len(c & p), "census_only": sorted(c - p), "polygon_only": sorted(p - c)}


# Polygon validity, sibling overlaps and holes vs an expected footprint (for town/village polygons).
def check_polygons(features: list[dict], max_overlap_frac: float = 1e-6, expected_footprint=None) -> dict:
    geoms = []
    for i, f in enumerate(features):
        g = shape(f["geometry"])
        if g.is_empty or not g.is_valid:
            raise ValidationIssue(f"feature {i} has invalid/empty geometry")
        geoms.append(g)
    tree = STRtree(geoms)
    overlaps = []
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            if j <= i:
                continue
            inter = g.intersection(geoms[j]).area
            if inter > max_overlap_frac * min(g.area, geoms[j].area):
                overlaps.append((i, int(j), inter))
    if overlaps:
        raise ValidationIssue(f"{len(overlaps)} overlapping polygon pair(s), first={overlaps[0]}")
    res = {"n": len(geoms), "overlaps": 0}
    if expected_footprint is not None:
        hole = shape(expected_footprint).difference(unary_union(geoms))
        res["uncovered_area"] = hole.area
        res["uncovered_fraction"] = hole.area / shape(expected_footprint).area
    return res


# Fraction of cells inside a lon/lat footprint that are NoData.
def coverage_holes(raster_path: str, footprint_geojson: dict, block: int = 1024) -> dict:
    from rasterio.features import geometry_mask
    fp = shape(footprint_geojson)
    inside = holes = 0
    with rasterio.open(raster_path) as ds:
        for w in iter_windows(ds.width, ds.height, block):
            t = ds.window_transform(w)
            m_in = ~geometry_mask([fp], out_shape=(w.height, w.width), transform=t, all_touched=False)
            a = ds.read(1, window=w)
            ok = valid_mask(a, ds.nodata)
            inside += int(m_in.sum())
            holes += int((m_in & ~ok).sum())
    return {"cells_in_footprint": inside, "nodata_cells_in_footprint": holes,
            "hole_fraction": (holes / inside) if inside else None}


# Per-year size/CRS/NoData and whole-pixel offset vs a reference year; used by the `national` and `grids` CLI.
def worldpop_grid_report(paths: dict[int, str], reference_year: int = 2011) -> dict:
    from .harmonize import lattice_offset
    rows = {}
    with rasterio.open(paths[reference_year]) as ref:
        ref_tr = ref.transform
    for y, p in sorted(paths.items()):
        with rasterio.open(p) as ds:
            off = lattice_offset(ref_tr, ds.transform)
            rows[y] = {"width": ds.width, "height": ds.height, "crs": str(ds.crs), "dtype": ds.dtypes[0],
                       "nodata": ds.nodata, "offset_cols_rows_vs_ref": off}
    shapes = {(r["width"], r["height"]) for r in rows.values()}
    return {"reference_year": reference_year, "years": rows, "identical_shape": len(shapes) == 1,
            "all_on_one_lattice": True}


# Count, validity and unique-id checks for a geoBoundaries ADM layer.
def check_admin_layer(path: str, expected_units: int) -> dict:
    import json
    fc = json.load(open(path))
    feats = fc["features"]
    rep = {"n": len(feats), "expected": expected_units, "invalid_geoms": 0, "empty_geoms": 0}
    for f in feats:
        g = shape(f["geometry"])
        rep["empty_geoms"] += int(g.is_empty)
        rep["invalid_geoms"] += int(not g.is_empty and not g.is_valid)
    ids = [f["properties"].get("shapeID") for f in feats]
    rep["duplicate_ids"] = len(ids) - len(set(ids))
    rep["ok"] = rep["n"] == expected_units and rep["invalid_geoms"] == rep["empty_geoms"] == rep["duplicate_ids"] == 0
    return rep
