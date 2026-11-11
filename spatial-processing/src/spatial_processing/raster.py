# Raster work: pixel-center selection, unit semantics and zonal statistics, and the clipped
# GeoTIFF/COG writer. A pixel is in the AOI iff its CENTER is inside the polygon (all_touched=false);
# statistics use native-resolution values in the raster's native CRS (the AOI is reprojected, never the raster).

from __future__ import annotations

import math
import os
import re
import time
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import rasterio
import rasterio.crs
import rasterio.errors
import rasterio.shutil
from affine import Affine
from pyproj import CRS
from rasterio.errors import NotGeoreferencedWarning
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.windows import Window
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry

from .config import ApiError

# Under thread concurrency rasterize() can emit a spurious NotGeoreferencedWarning from its
# temporary in-memory dataset before our transform is applied; the masks are unaffected.
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning, module="rasterio.features")

PIXEL_SELECTION = {
    "rule": "pixel_center",
    "all_touched": False,
    "description": "A pixel is included only if its center point falls inside the AOI polygon "
    "(GDAL/rasterio all_touched=false). Statistics use native-resolution values in the raster's "
    "native CRS without resampling.",
}

# Deadline: called with a stage name between processing stages; raises 504 JOB_TIMEOUT when over time.
Deadline = Callable[[str], None]


# A Deadline that never expires. Default of select_pixels/write_clip for callers without a time limit.
def _no_deadline(stage: str) -> None:
    return None


# Return a Deadline that expires `seconds` from now. Used by service._run.
def monotonic_deadline(seconds: float) -> Deadline:
    end = time.monotonic() + seconds

    # Raise JOB_TIMEOUT if the time limit has passed. Called between stages below.
    def check(stage: str) -> None:
        if time.monotonic() > end:
            raise ApiError(504, "JOB_TIMEOUT", f"the operation exceeded the {seconds:g}s time limit (stage: {stage})")

    return check


# --------------------------------------------------------------------------- units
# What a zonal "sum" means: count units (each cell holds a count) give a total count; density
# units (per km2, ...) cannot be summed without area weighting, so the sum is omitted (or refused
# when explicitly requested); anything else is a plain sum of cell values with a warning.

COUNT_UNITS = {
    "people_per_cell",
    "persons_per_cell",
    "population_per_cell",
    "count_per_cell",
    "count",
    "counts",
    "people",
    "persons",
    "population_count",
}
SUM_SEMANTICS = {"count": "total_count", "density": "not_applicable_density", "unknown": "sum_of_cell_values"}
_DENSITY_RE = re.compile(
    r"(per[_ ]?(sq[_ ]?)?(km|m|mi|ha|hectare|acre)s?(2|\^2|²)?\b|/\s*(km|m|mi|ha)|density|per[_ ]square)",
    re.IGNORECASE,
)


# Classify catalog units as "count", "density" or "unknown". Used by summarize_statistics and
# service.zonal_statistics.
def classify_units(units: str | None) -> str:
    normalized = (units or "").strip().lower()
    if normalized in COUNT_UNITS:
        return "count"
    if normalized and _DENSITY_RE.search(normalized):
        return "density"
    return "unknown"


# --------------------------------------------------------------------------- reading


# Bound GDAL's block cache and skip sibling-file scans (.aux.xml/.ovr) on open. Used by service
# (all raster access) and the application-api export worker.
@contextmanager
def gdal_env(cachemax_mb: int) -> Iterator[None]:
    with rasterio.Env(
        GDAL_CACHEMAX=cachemax_mb, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_TIFF_INTERNAL_MASK=True
    ):
        yield


# Open a catalogued GeoTIFF with the GTiff driver only (a VRT could pull in other files);
# 500 ASSET_UNREADABLE otherwise. Used by service (all raster access) and the application-api export worker.
def open_raster(path: Path):
    try:
        src = rasterio.open(path, driver="GTiff")
    except rasterio.errors.RasterioIOError as exc:
        raise ApiError(500, "ASSET_UNREADABLE", "the dataset's raster file could not be read") from exc
    if src.driver != "GTiff":
        src.close()
        raise ApiError(500, "ASSET_UNREADABLE", "the dataset's raster file is not a GeoTIFF")
    return src


# Pixels selected by an AOI: the trimmed window plus per-pixel "inside AOI" and "invalid" masks.
@dataclass
class Selection:
    window: Window  # selected pixel extent in full-raster coordinates
    transform: Affine  # transform of `window`
    values: np.ndarray  # raw values in `window`
    inside: np.ndarray  # bool, pixel center inside AOI
    invalid: np.ndarray  # bool, NoData / masked / non-finite
    crs: CRS
    nodata: float | None

    # Pixels inside the AOI that hold data. Used by zonal_statistics, write_clip and service.clip.
    @property
    def valid(self) -> np.ndarray:
        return self.inside & ~self.invalid


# The AOI's bounds in pixel space, rounded outwards (+1 pixel pad against float error) and clamped;
# no pixel is read. Rejects multi-band, CRS-less and rotated rasters and AOIs that miss the raster.
# Used by select_pixels and the blockwise module.
def candidate_window(src, aoi_native: BaseGeometry) -> Window:
    if src.count != 1:
        raise ApiError(
            422, "UNSUPPORTED_OPERATION", f"only single-band rasters are supported (dataset has {src.count} bands)"
        )
    if src.crs is None:
        raise ApiError(500, "ASSET_UNREADABLE", "the dataset's raster file has no CRS")
    if src.transform.b != 0 or src.transform.d != 0:
        raise ApiError(422, "UNSUPPORTED_OPERATION", "rotated/sheared raster grids are not supported")
    if not aoi_native.intersects(box(*src.bounds)):
        raise ApiError(
            422,
            "AOI_OUTSIDE_RASTER",
            "the AOI does not overlap the dataset's raster extent",
            {"raster_bounds_native": list(src.bounds)},
        )

    inv = ~src.transform
    minx, miny, maxx, maxy = aoi_native.bounds
    cols, rows = zip(*(inv @ (x, y) for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))))
    col0 = max(0, math.floor(min(cols)) - 1)
    col1 = min(src.width, math.ceil(max(cols)) + 1)
    row0 = max(0, math.floor(min(rows)) - 1)
    row1 = min(src.height, math.ceil(max(rows)) + 1)
    if col1 <= col0 or row1 <= row0:
        raise ApiError(422, "AOI_OUTSIDE_RASTER", "the AOI does not overlap the dataset's raster extent")
    return Window(col0, row0, col1 - col0, row1 - row0)


# Boolean mask of the pixels of `window` whose centers are inside the GeoJSON geometry
# (all_touched=false). Used by select_pixels and the blockwise module.
def center_mask(src, geom_json: dict, window: Window) -> np.ndarray:
    return geometry_mask(
        [geom_json],
        out_shape=(int(window.height), int(window.width)),
        transform=src.window_transform(window),
        all_touched=False,
        invert=True,
    )


# Read band 1 in `window`; returns (values, invalid) where invalid marks file NoData/masked,
# non-finite and catalog-NoData pixels. Used by select_pixels and the blockwise module.
def read_values(src, window: Window, catalog_nodata: float | None) -> tuple[np.ndarray, np.ndarray]:
    masked = src.read(1, window=window, masked=True)
    values = np.ma.getdata(masked)
    invalid = np.ma.getmaskarray(masked).copy()
    if np.issubdtype(values.dtype, np.floating):
        invalid |= ~np.isfinite(values)
    if catalog_nodata is not None and src.nodata != catalog_nodata:
        # Honour the catalog's NoData too; never count a declared NoData value as data.
        invalid |= values == np.asarray(catalog_nodata).astype(values.dtype)
    return values, invalid


# 422 for an AOI that overlaps the raster without containing any pixel center.
# Used by select_pixels and the blockwise module.
def no_pixels_error() -> ApiError:
    return ApiError(
        422,
        "NO_PIXELS_SELECTED",
        "the AOI overlaps the raster but contains no pixel centers (pixel-center rule); use a larger AOI",
    )


# Select pixels whose centers fall in `aoi_native` (already in the raster CRS), enforcing the
# window-size limit and honouring file and catalog NoData. Used by service._open_selection and the
# application-api export worker.
def select_pixels(
    src,
    aoi_native: BaseGeometry,
    *,
    max_window_pixels: int,
    catalog_nodata: float | None = None,
    deadline: Deadline = _no_deadline,
) -> Selection:
    candidate = candidate_window(src, aoi_native)
    col0, row0 = int(candidate.col_off), int(candidate.row_off)
    pixels = int(candidate.width) * int(candidate.height)
    if pixels > max_window_pixels:
        raise ApiError(
            413,
            "AOI_TOO_LARGE",
            f"the AOI covers more than {max_window_pixels} raster pixels; use a smaller AOI",
            {"pixels": pixels, "limit": max_window_pixels},
        )
    inside = center_mask(src, mapping(aoi_native), candidate)
    if not inside.any():
        raise no_pixels_error()
    deadline("select")

    # Trim to the exact extent of selected pixels so clip dimensions are deterministic.
    sel_rows = np.flatnonzero(inside.any(axis=1))
    sel_cols = np.flatnonzero(inside.any(axis=0))
    r0, r1 = int(sel_rows[0]), int(sel_rows[-1]) + 1
    c0, c1 = int(sel_cols[0]), int(sel_cols[-1]) + 1
    inside = inside[r0:r1, c0:c1]
    window = Window(col0 + c0, row0 + r0, c1 - c0, r1 - r0)

    values, invalid = read_values(src, window, catalog_nodata)
    deadline("read")

    return Selection(
        window=window,
        transform=src.window_transform(window),
        values=values,
        inside=inside,
        invalid=invalid,
        crs=CRS.from_user_input(src.crs),
        nodata=src.nodata if src.nodata is not None else catalog_nodata,
    )


# Sum of valid values as int (integer rasters) or float, None when there are no values.
# Used by zonal_statistics and write_clip.
def _total(values: np.ndarray):
    if not values.size:
        return None
    integer = np.issubdtype(values.dtype, np.integer)
    total = values.astype(np.int64 if integer else np.float64).sum()
    return int(total) if integer else float(total)


# --------------------------------------------------------------------------- statistics


# count/sum/mean of the selection's valid pixels. Used by service._zonal_work (in-memory path).
def zonal_statistics(sel: Selection, units: str | None, operations: list[str]) -> dict[str, Any]:
    valid = sel.valid
    count_valid = int(valid.sum())
    values = sel.values[valid]
    mean = float(values.astype(np.float64).mean()) if count_valid else None
    return summarize_statistics(count_valid, int(sel.inside.sum()), _total(values), mean, units, operations)


# The zonal result: statistics plus NoData accounting and unit-aware sum semantics; fields not in
# `operations` are null. Used by zonal_statistics and blockwise.zonal_blockwise.
def summarize_statistics(
    count_valid: int, pixels_in_aoi: int, total, mean: float | None, units: str | None, operations: list[str]
) -> dict[str, Any]:
    result_warnings: list[str] = []
    if not count_valid:
        result_warnings.append("All pixels selected by the AOI are NoData; sum and mean are undefined (null).")
    kind = classify_units(units)
    if kind == "density":
        total = None
        result_warnings.append(
            f"Units '{units}' are a density; a sum of cell values is not a count and is omitted. "
            "Area-weighted totals are not computed."
        )
    elif kind == "unknown":
        result_warnings.append(
            f"Units '{units}' are not a recognised count unit; 'sum' is the plain sum of cell "
            "values and must not be interpreted as a population count."
        )

    return {
        "count_valid": count_valid if "count" in operations else None,
        "sum": total if "sum" in operations else None,
        "mean": mean if "mean" in operations else None,
        "pixels_in_aoi": pixels_in_aoi,
        "nodata_excluded": pixels_in_aoi - count_valid,
        "sum_semantics": SUM_SEMANTICS[kind],
        "warnings": result_warnings,
    }


# --------------------------------------------------------------------------- export


# Reproject a clip to `dst_crs` with nearest-neighbour onto a new grid; returns (data, mask, transform).
# Used by write_clip when output_crs differs from the native CRS.
def _reproject(data, mask, transform, src_crs, dst_crs, nodata):
    height, width = data.shape
    left, top = transform @ (0, 0)
    right, bottom = transform @ (width, height)
    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, width, height, left=left, bottom=bottom, right=right, top=top
    )
    dst = np.full((dst_h, dst_w), np.asarray(nodata if nodata is not None else 0).astype(data.dtype))
    common = dict(src_transform=transform, src_crs=src_crs, dst_transform=dst_transform, dst_crs=dst_crs,
                  resampling=Resampling.nearest)
    reproject(source=data, destination=dst, src_nodata=nodata, dst_nodata=nodata, **common)
    dst_mask = np.zeros((dst_h, dst_w), dtype=np.uint8)
    reproject(source=mask, destination=dst_mask, **common)
    return dst, dst_mask, dst_transform


# Write the selected pixels (outside-AOI and NoData pixels become NoData, or an internal mask when
# the source has no NoData) to `out_path` as GTiff or COG via a temp file + atomic rename, then
# reopen it and return the measured output metadata. Used by service._clip_work (in-memory path)
# and the application-api export worker.
def write_clip(
    src,
    sel: Selection,
    out_path: Path,
    *,
    fmt: str,
    output_crs: CRS | None,
    tags: dict[str, str],
    units: str | None,
    deadline: Deadline = _no_deadline,
) -> dict[str, Any]:
    nodata = sel.nodata
    keep = sel.valid
    data = sel.values.copy()
    if nodata is not None:
        data[~keep] = np.asarray(nodata).astype(data.dtype)
    mask = np.where(keep, 255, 0).astype(np.uint8)
    dtype = str(sel.values.dtype)

    dst_crs = output_crs or sel.crs
    transform = sel.transform
    resampled = not dst_crs.equals(sel.crs, ignore_axis_order=True)
    result_warnings: list[str] = []
    if resampled:
        data, mask, transform = _reproject(data, mask, transform, sel.crs, dst_crs, nodata)
        result_warnings.append(
            "Output was reprojected to the requested output_crs with nearest-neighbour resampling onto a "
            "new grid; cell values are copied, not area-weighted, so totals of count rasters are NOT "
            "preserved. Use the native CRS (omit output_crs) for analytical exports."
        )
    deadline("reproject")

    profile = {
        "driver": "GTiff",
        "dtype": dtype,
        "count": 1,
        "width": data.shape[1],
        "height": data.shape[0],
        # Keep the source CRS definition verbatim unless a different CRS was requested.
        "crs": rasterio.crs.CRS.from_wkt(dst_crs.to_wkt()) if resampled else src.crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "DEFLATE",
        "predictor": predictor(dtype),
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    tmp_gtiff = out_path.parent / f".tmp-{uuid.uuid4().hex}.tif"
    tmp_cog = out_path.parent / f".tmp-{uuid.uuid4().hex}.cog.tif"
    try:
        with rasterio.open(tmp_gtiff, "w", **profile) as dst_ds:
            dst_ds.write(data, 1)
            if nodata is None:
                dst_ds.write_mask(mask)
            dst_ds.update_tags(**tags)
            dst_ds.set_band_description(1, src.descriptions[0] or tags.get("DATASET_ID", ""))
            if units:
                dst_ds.set_band_unit(1, units)
            dst_ds.scales = src.scales
            dst_ds.offsets = src.offsets
        deadline("write")
        if fmt == "COG":
            rasterio.shutil.copy(
                tmp_gtiff, tmp_cog, driver="COG", COMPRESS="DEFLATE", PREDICTOR="YES", BLOCKSIZE=256, OVERVIEWS="NONE"
            )
            os.replace(tmp_cog, out_path)
        else:
            os.replace(tmp_gtiff, out_path)
    finally:
        tmp_gtiff.unlink(missing_ok=True)
        tmp_cog.unlink(missing_ok=True)

    # Round-trip check: reopen what was written and measure it.
    with rasterio.open(out_path) as check:
        arr = check.read(1, masked=True)
        valid = ~np.ma.getmaskarray(arr)
        if np.issubdtype(arr.dtype, np.floating):
            valid &= np.isfinite(np.ma.getdata(arr))
        meta = describe_output(check, out_path, fmt, resampled, int(valid.sum()), _total(np.ma.getdata(arr)[valid]))
    meta["warnings"] = result_warnings
    return meta


# GDAL predictor for DEFLATE: 3 (floating point) or 2 (integer). Used by write_clip and blockwise.clip_blockwise.
def predictor(dtype: str) -> int:
    return 3 if np.issubdtype(np.dtype(dtype), np.floating) else 2


# Metadata of a written clip measured from the reopened file `check` (count/sum of its valid pixels
# are passed in). Used by write_clip and blockwise.clip_blockwise.
def describe_output(check, path: Path, fmt: str, resampled: bool, count_valid: int, sum_valid) -> dict[str, Any]:
    epsg = check.crs.to_epsg()
    t = check.transform
    return {
        "format": fmt,
        "driver_layout": check.tags(ns="IMAGE_STRUCTURE").get("LAYOUT", "GTiff"),
        "width": check.width,
        "height": check.height,
        "crs": f"EPSG:{epsg}" if epsg else check.crs.to_wkt(),
        "transform_gdal": [t.c, t.a, t.b, t.f, t.d, t.e],
        "bounds": list(check.bounds),
        "nodata": check.nodata,
        "dtype": check.dtypes[0],
        "mask": "nodata_value" if check.nodata is not None else "internal_mask_band",
        "count_valid": count_valid,
        "sum_valid": sum_valid,
        "resampled": resampled,
        "resampling": "nearest" if resampled else "none",
        "size_bytes": path.stat().st_size,
    }
