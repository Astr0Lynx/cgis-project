"""Blockwise weighting, Census normalization and matched-support diagnostics."""

from contextlib import ExitStack
from time import perf_counter

import numpy as np
import rasterio
from rasterio.shutil import copy as copy_raster
from rasterio.windows import Window

from ingestion.raster import COG_OPTIONS

from .grid import BLOCK, CRS, PIXEL, CoarseGrid, FineGrid, owned_cells
from .inputs import LULC_NODATA, MODELS, RasterLandCover, VectorLandCover

NODATA = -99999.0
MASS_ABS_TOL = 1e-3
MASS_REL_TOL = 1e-6


def block_inputs(grid, window, town, neighbors, land, legend, coarse):
    cells = owned_cells(grid, window, town.census_id, town.polygon, neighbors)
    codes = land.read(window)[cells.rows, cells.cols]
    unique = np.unique(codes)
    unknown = set(unique) - set(legend) - {LULC_NODATA}
    if unknown:
        raise ValueError(f"unknown LULC raster classes: {sorted(unknown)}")
    fi, ci, areas = coarse.overlaps(cells)
    prior = np.bincount(fi, weights=areas * coarse.density.ravel()[ci], minlength=len(codes))
    weights = np.zeros((len(MODELS), len(codes)))
    for code in unique:
        if code != LULC_NODATA:
            land_class = legend[code]
            weights[:, codes == code] = np.array([land_class.soft, land_class.hard])[:, None]
    return cells, codes, prior, weights, (fi, ci, areas)


def _write_comparison(path, coarse, src, common_area, level2):
    common = common_area > 0
    level1 = common_area * coarse.density.ravel()
    delta = level2 - level1
    shape = coarse.shape
    native_affine = src.window_transform(Window(coarse.col0, coarse.row0, shape[1], shape[0]))
    with rasterio.open(path, "w", driver="GTiff", dtype="float64", crs=src.crs,
                       transform=native_affine, width=shape[1], height=shape[0], count=3,
                       nodata=float("nan"), tiled=True, blockxsize=BLOCK, blockysize=BLOCK,
                       compress="deflate", predictor=3) as dst:
        for i, (label, data) in enumerate(zip(("level1_common", "level2_common", "level2_minus_level1"),
                                               (level1, level2, delta)), 1):
            dst.write(np.where(common, data, np.nan).reshape(shape), i)
            dst.set_band_description(i, label)
            dst.set_band_unit(i, "people_per_native_cell_intersection")
    return level1[common], level2[common], delta[common]


def _comparison_metrics(level1, level2, delta):
    if not len(delta):
        raise ValueError("no common Level 1 and classified Level 2 support")
    a, b = float(np.sum(level1)), float(np.sum(level2))
    return {
        "native_cells": len(delta),
        "level1_people": a,
        "level2_people": b,
        "bias_people_per_native_cell": float(np.mean(delta)),
        "mae_people_per_native_cell": float(np.mean(np.abs(delta))),
        "rmse_people_per_native_cell": float(np.sqrt(np.mean(delta ** 2))),
        "total_delta_people": b - a,
        "relative_total_delta": (b - a) / a if a > 0 else None,
        "relative_absolute_difference": float(np.sum(np.abs(delta))) / a if a > 0 else None,
    }


def _cog_stats(path):
    valid_cells, total = 0, 0.0
    with rasterio.open(path) as src:
        if src.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG":
            raise ValueError("output TIFF is missing the COG layout")
        for _, win in src.block_windows(1):
            valid = src.read(1, window=win, masked=True).compressed()
            if np.any(valid < 0) or np.any(~np.isfinite(valid)):
                raise ValueError("invalid stored population")
            valid_cells += valid.size
            total += float(np.sum(valid, dtype="float64"))
    return valid_cells, total


def build_town(stage, town, neighbors, src, band, land_source, legend, method, guard):
    started = perf_counter()
    grid = FineGrid.for_polygon(town.polygon)
    coarse = CoarseGrid(src, band, grid, town.polygon)
    selected = MODELS.index(method["primary"])
    observed_area = 0.0
    classified_area = 0.0
    raw_sums = np.zeros((2, len(MODELS)))  # prior × class, then area × class
    class_area = dict.fromkeys(legend, 0.0)
    with ExitStack() as stack:
        if land_source.metadata["kind"] == "raster":
            land = RasterLandCover(stack, land_source, grid, legend)
        else:
            land = VectorLandCover(land_source, grid, legend)
        for window in grid.windows():
            guard()
            cells, codes, prior, weights, _ = block_inputs(grid, window, town, neighbors, land, legend, coarse)
            observed_area += float(np.sum(cells.area))
            classified_area += float(np.sum(cells.area[codes != LULC_NODATA]))
            raw_sums[0] += np.sum(weights * prior, axis=1)
            raw_sums[1] += np.sum(weights * cells.area, axis=1)
            for code in np.unique(codes):
                if code != LULC_NODATA:
                    class_area[code] += float(np.sum(cells.area[codes == code]))
        if observed_area <= 0 or classified_area <= 0:
            raise ValueError("settlement has no owned or classified LULC support")
        lulc_coverage = min(1.0, classified_area / town.polygon.area)
        level1_coverage = min(1.0, coarse.town_valid_area / town.polygon.area)
        if (lulc_coverage < method["minimum_lulc_coverage"]
                or level1_coverage < method["minimum_level1_coverage"]):
            raise ValueError("settlement does not meet the configured source-coverage requirements")
        fallback = raw_sums[0] == 0
        denominators = np.where(fallback, raw_sums[1], raw_sums[0])
        possible = (denominators > 0) | (town.population == 0)
        if not possible[selected]:
            raise ValueError("positive Census total has no admissible cells under the primary method")
        normalization = np.divide(town.population, denominators, out=np.zeros_like(denominators),
                                  where=denominators > 0)
        common_area = np.zeros(coarse.density.size)
        coarse_counts = np.zeros(coarse.density.size)
        class_people = {code: np.zeros(len(MODELS)) for code in legend}
        shifted = 0.0
        raw = stage / "fine.tif"
        with rasterio.open(raw, "w", driver="GTiff", dtype="float32", crs=CRS, transform=grid.transform,
                           width=grid.width, height=grid.height, count=1, nodata=NODATA, tiled=True,
                           blockxsize=BLOCK, blockysize=BLOCK, compress="deflate", predictor=3) as dst:
            dst.set_band_unit(1, "people_per_cell")
            dst.update_tags(year="2011", census_id=town.census_id)
            for window in grid.windows():
                guard()
                cells, codes, prior, weights, pairs = block_inputs(grid, window, town, neighbors, land, legend, coarse)
                fi, ci, areas = pairs
                known = codes != LULC_NODATA
                mass = weights * np.where(fallback[:, None], cells.area, prior) * normalization[:, None]
                stored = mass[selected].astype("float32")
                tile = np.full((int(window.height), int(window.width)), NODATA, dtype="float32")
                tile[cells.rows[known], cells.cols[known]] = stored[known]
                dst.write(tile, 1, window=window)
                use = known[fi]
                np.add.at(common_area, ci[use], areas[use])
                np.add.at(coarse_counts, ci[use], stored[fi[use]] * areas[use] / cells.area[fi[use]])
                for code in np.unique(codes[known]):
                    class_people[code] += mass[:, codes == code].sum(axis=1)
                if possible.all():
                    shifted += float(np.sum(np.abs(mass[0] - mass[1])) / 2)
    cog = stage / "population.tif"
    copy_raster(raw, cog, driver="COG", **COG_OPTIONS, NUM_THREADS="1")
    raw.unlink()
    valid_cells, total = _cog_stats(cog)
    mass_error = total - town.population
    tolerance = max(MASS_ABS_TOL, MASS_REL_TOL * town.population)
    if valid_cells == 0 or abs(mass_error) > tolerance:
        raise ValueError("stored Level 2 raster failed Census mass balance")
    level1, level2, delta = _write_comparison(stage / "comparison.tif", coarse, src, common_area, coarse_counts)
    return {
        "census_id": town.census_id, "name": town.name, "slug": town.slug, "region": town.region,
        "morphology": town.morphology, "census_people": town.population, "stored_level2_people": total,
        "signed_census_error_people": mass_error, "absolute_census_error_people": abs(mass_error),
        "relative_census_error": mass_error / town.population if town.population else None,
        "mass_tolerance_people": tolerance, "valid_fine_cells": valid_cells,
        "polygon_area_m2": town.polygon.area, "owned_polygon_area_m2": observed_area,
        "unassigned_edge_area_m2": max(0.0, town.polygon.area - observed_area),
        "classified_area_m2": classified_area, "lulc_coverage_of_census_polygon": lulc_coverage,
        "level1_coverage_of_census_polygon": level1_coverage,
        "common_area_m2": float(common_area.sum()),
        "common_coverage_of_census_polygon": min(1.0, float(common_area.sum()) / town.polygon.area),
        "level2_people_outside_comparison_support": max(0.0, total - float(level2.sum())),
        "level1_people_on_census_polygon_observed_support": coarse.town_people,
        "level1_minus_census_people": coarse.town_people - town.population,
        "level1_relative_difference_vs_census": (coarse.town_people / town.population - 1) if town.population else None,
        "matched_level1_comparison": _comparison_metrics(level1, level2, delta),
        "primary_method": method["primary"],
        "sensitivity": {
            "model_status": {m: ("available" if possible[i] else "no_eligible_cells") for i, m in enumerate(MODELS)},
            "area_class_fallback": {m: bool(fallback[i] and town.population > 0 and possible[i])
                                    for i, m in enumerate(MODELS)},
            "people_shifted_between_soft_and_hard": shifted if possible.all() else None,
            "fraction_shifted_between_soft_and_hard": shifted / town.population
            if possible.all() and town.population else None,
            "classes": [
                {"code": code, "label": rule.label, "area_m2": class_area[code],
                 "soft_weight": rule.soft, "hard_eligible": bool(rule.hard),
                 "people_by_model": {m: float(class_people[code][i]) if possible[i] else None
                                     for i, m in enumerate(MODELS)}}
                for code, rule in sorted(legend.items())
            ],
        },
        "grid": {"crs": CRS, "transform": list(grid.transform)[:6], "resolution": [PIXEL, PIXEL],
                 "bounds": list(grid.bounds), "width": grid.width, "height": grid.height, "nodata": NODATA},
        "elapsed_seconds_model": perf_counter() - started,
    }
