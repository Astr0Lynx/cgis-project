"""Raster pixel selection, zonal statistics and clipped GeoTIFF/COG export.

Pixel-inclusion policy (analytical): a pixel belongs to the AOI iff its CENTER lies
inside the AOI polygon (``rasterio.features.geometry_mask(..., all_touched=False)``).
No resampling is performed for statistics; values are read at native resolution in
the raster's native CRS (the AOI is transformed to the raster CRS, never the reverse).
"""

from __future__ import annotations

import math
import os
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

from . import errors
from .errors import ApiError
from .units import UnitSemantics

# rasterize() builds a temporary in-memory GDAL dataset and, under thread concurrency, can emit a
# spurious NotGeoreferencedWarning before applying the transform we pass. Masks are unaffected
# (verified with a 2000-iteration, 16-thread stress test against a reference mask).
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning, module="rasterio.features")

PIXEL_SELECTION = {
    "rule": "pixel_center",
    "all_touched": False,
    "description": "A pixel is included only if its center point falls inside the AOI polygon "
    "(GDAL/rasterio all_touched=false). Statistics use native-resolution values in the raster's "
    "native CRS without resampling.",
}

Deadline = Callable[[str], None]


def _no_deadline(stage: str) -> None:
    return None


@contextmanager
def gdal_env(cachemax_mb: int = 256) -> Iterator[None]:
    # Bound GDAL's block cache and don't scan sibling files (.aux.xml/.ovr) on open.
    with rasterio.Env(
        GDAL_CACHEMAX=cachemax_mb,
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_TIFF_INTERNAL_MASK=True,
    ):
        yield


def open_raster(path: Path):
    """Open a catalogued GeoTIFF; refuse other drivers (e.g. VRT that could reference other files)."""
    try:
        src = rasterio.open(path, driver="GTiff")
    except rasterio.errors.RasterioIOError as exc:
        raise ApiError(500, errors.ASSET_UNREADABLE, "the dataset's raster file could not be read") from exc
    if src.driver != "GTiff":
        src.close()
        raise ApiError(500, errors.ASSET_UNREADABLE, "the dataset's raster file is not a GeoTIFF")
    return src


@dataclass
class Selection:
    window: Window  # selected pixel extent in full-raster coordinates
    transform: Affine  # transform of `window`
    values: np.ndarray  # raw values in `window`
    inside: np.ndarray  # bool, pixel center inside AOI
    invalid: np.ndarray  # bool, NoData / masked / non-finite
    crs: CRS
    nodata: float | None
    dtype: str

    @property
    def valid(self) -> np.ndarray:
        return self.inside & ~self.invalid


def _check_grid(src) -> None:
    if src.count != 1:
        raise ApiError(
            422,
            errors.UNSUPPORTED_OPERATION,
            f"only single-band rasters are supported (dataset has {src.count} bands)",
        )
    if src.crs is None:
        raise ApiError(500, errors.ASSET_UNREADABLE, "the dataset's raster file has no CRS")
    t = src.transform
    if t.b != 0 or t.d != 0:
        raise ApiError(422, errors.UNSUPPORTED_OPERATION, "rotated/sheared raster grids are not supported")


def select_pixels(
    src,
    aoi_native: BaseGeometry,
    *,
    max_window_pixels: int,
    catalog_nodata: float | None = None,
    deadline: Deadline = _no_deadline,
) -> Selection:
    """Select pixels whose centers fall in ``aoi_native`` (AOI already in the raster CRS)."""
    _check_grid(src)
    raster_box = box(*src.bounds)
    if not aoi_native.intersects(raster_box):
        raise ApiError(
            422,
            errors.AOI_OUTSIDE_RASTER,
            "the AOI does not overlap the dataset's raster extent",
            {"raster_bounds_native": list(src.bounds)},
        )

    # Candidate window: AOI bounds rounded outwards (+1 pixel pad against float error), clamped.
    inv = ~src.transform
    minx, miny, maxx, maxy = aoi_native.bounds
    cols, rows = zip(*(inv @ (x, y) for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))))
    col0 = max(0, math.floor(min(cols)) - 1)
    col1 = min(src.width, math.ceil(max(cols)) + 1)
    row0 = max(0, math.floor(min(rows)) - 1)
    row1 = min(src.height, math.ceil(max(rows)) + 1)
    if col1 <= col0 or row1 <= row0:
        raise ApiError(422, errors.AOI_OUTSIDE_RASTER, "the AOI does not overlap the dataset's raster extent")
    if (col1 - col0) * (row1 - row0) > max_window_pixels:
        raise ApiError(
            413,
            errors.AOI_TOO_LARGE,
            f"the AOI covers more than {max_window_pixels} raster pixels; use a smaller AOI",
            {"pixels": (col1 - col0) * (row1 - row0), "limit": max_window_pixels},
        )
    candidate = Window(col0, row0, col1 - col0, row1 - row0)
    inside = geometry_mask(
        [mapping(aoi_native)],
        out_shape=(int(candidate.height), int(candidate.width)),
        transform=src.window_transform(candidate),
        all_touched=False,
        invert=True,
    )
    if not inside.any():
        raise ApiError(
            422,
            errors.NO_PIXELS_SELECTED,
            "the AOI overlaps the raster but contains no pixel centers (pixel-center rule); "
            "use a larger AOI",
        )
    deadline("select")

    # Trim to the exact extent of selected pixels so clip dimensions are deterministic.
    sel_rows = np.flatnonzero(inside.any(axis=1))
    sel_cols = np.flatnonzero(inside.any(axis=0))
    r0, r1 = int(sel_rows[0]), int(sel_rows[-1]) + 1
    c0, c1 = int(sel_cols[0]), int(sel_cols[-1]) + 1
    inside = inside[r0:r1, c0:c1]
    window = Window(col0 + c0, row0 + r0, c1 - c0, r1 - r0)

    masked = src.read(1, window=window, masked=True)
    values = np.ma.getdata(masked)
    invalid = np.ma.getmaskarray(masked).copy()
    if np.issubdtype(values.dtype, np.floating):
        invalid |= ~np.isfinite(values)
    if catalog_nodata is not None and src.nodata != catalog_nodata:
        # Honour the catalog's NoData too; never count a declared NoData value as data.
        invalid |= values == np.asarray(catalog_nodata).astype(values.dtype)
    deadline("read")

    nodata = src.nodata if src.nodata is not None else catalog_nodata
    return Selection(
        window=window,
        transform=src.window_transform(window),
        values=values,
        inside=inside,
        invalid=invalid,
        crs=CRS.from_user_input(src.crs),
        nodata=nodata,
        dtype=str(values.dtype),
    )


def _as_number(value: Any, integer: bool):
    if value is None:
        return None
    return int(value) if integer else float(value)


def zonal_statistics(sel: Selection, semantics: UnitSemantics, operations: list[str]) -> dict[str, Any]:
    valid = sel.valid
    count_valid = int(valid.sum())
    pixels_in_aoi = int(sel.inside.sum())
    nodata_excluded = pixels_in_aoi - count_valid
    integer = np.issubdtype(sel.values.dtype, np.integer)
    values = sel.values[valid].astype(np.int64 if integer else np.float64)

    warnings: list[str] = []
    total = None
    mean = None
    if count_valid:
        total = values.sum()
        mean = float(values.mean())
    else:
        warnings.append("All pixels selected by the AOI are NoData; sum and mean are undefined (null).")

    sum_semantics = semantics.sum_semantics
    if semantics.kind == "density":
        total = None
        warnings.append(
            f"Units '{semantics.units}' are a density; a sum of cell values is not a count and is omitted. "
            "Area-weighted totals are not computed in Phase 2."
        )
    elif semantics.kind == "unknown":
        warnings.append(
            f"Units '{semantics.units}' are not a recognised count unit; 'sum' is the plain sum of cell "
            "values and must not be interpreted as a population count."
        )

    return {
        "count_valid": count_valid if "count" in operations else None,
        "sum": _as_number(total, bool(integer)) if "sum" in operations else None,
        "mean": mean if "mean" in operations else None,
        "pixels_in_aoi": pixels_in_aoi,
        "nodata_excluded": nodata_excluded,
        "sum_semantics": sum_semantics,
        "warnings": warnings,
    }


def _gdal_transform(t: Affine) -> list[float]:
    return [t.c, t.a, t.b, t.f, t.d, t.e]


def _predictor(dtype: str) -> int:
    return 3 if np.issubdtype(np.dtype(dtype), np.floating) else 2


def estimate_bytes(sel: Selection) -> int:
    return int(sel.values.size * np.dtype(sel.dtype).itemsize)


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
    """Write the selected pixels (outside-AOI and NoData pixels -> NoData/mask) to ``out_path``.

    The write goes to a unique temporary file in the same directory and is atomically
    renamed, so concurrent or failed jobs never leave a half-written result in place.
    """
    nodata = sel.nodata
    keep = sel.valid
    data = sel.values.copy()
    if nodata is not None:
        data[~keep] = np.asarray(nodata).astype(data.dtype)
    mask = np.where(keep, 255, 0).astype(np.uint8)

    src_crs = sel.crs
    dst_crs = output_crs or src_crs
    transform = sel.transform
    height, width = data.shape
    resampled = False
    warnings: list[str] = []

    if not dst_crs.equals(src_crs, ignore_axis_order=True):
        resampled = True
        left, top = transform @ (0, 0)
        right, bottom = transform @ (width, height)
        dst_transform, dst_w, dst_h = calculate_default_transform(
            src_crs, dst_crs, width, height, left=left, bottom=bottom, right=right, top=top
        )
        dst = np.empty((dst_h, dst_w), dtype=data.dtype)
        fill = np.asarray(nodata if nodata is not None else 0).astype(data.dtype)
        dst.fill(fill)
        reproject(
            source=data,
            destination=dst,
            src_transform=transform,
            src_crs=src_crs,
            src_nodata=nodata,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=nodata,
            resampling=Resampling.nearest,
        )
        dst_mask = np.zeros((dst_h, dst_w), dtype=np.uint8)
        reproject(
            source=mask,
            destination=dst_mask,
            src_transform=transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.nearest,
        )
        data, mask, transform, height, width = dst, dst_mask, dst_transform, dst_h, dst_w
        warnings.append(
            "Output was reprojected to the requested output_crs with nearest-neighbour resampling onto a "
            "new grid; cell values are copied, not area-weighted, so totals of count rasters are NOT "
            "preserved. Use the native CRS (omit output_crs) for analytical exports."
        )
    deadline("reproject")

    profile = {
        "driver": "GTiff",
        "dtype": sel.dtype,
        "count": 1,
        "width": width,
        "height": height,
        # Keep the source CRS definition verbatim unless the caller asked for a different CRS.
        "crs": src.crs if not resampled else rasterio.crs.CRS.from_wkt(dst_crs.to_wkt()),
        "transform": transform,
        "nodata": nodata,
        "compress": "DEFLATE",
        "predictor": _predictor(sel.dtype),
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    out_dir = out_path.parent
    tmp_gtiff = out_dir / f".tmp-{uuid.uuid4().hex}.tif"
    tmp_cog = out_dir / f".tmp-{uuid.uuid4().hex}.cog.tif"
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
                tmp_gtiff,
                tmp_cog,
                driver="COG",
                COMPRESS="DEFLATE",
                PREDICTOR="YES",
                BLOCKSIZE=256,
                OVERVIEWS="NONE",
            )
            os.replace(tmp_cog, out_path)
        else:
            os.replace(tmp_gtiff, out_path)
    finally:
        for leftover in (tmp_gtiff, tmp_cog):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass

    # Round-trip verification: reopen what we wrote and recompute basic stats.
    with rasterio.open(out_path) as check:
        arr = check.read(1, masked=True)
        valid = ~np.ma.getmaskarray(arr)
        if np.issubdtype(arr.dtype, np.floating):
            valid &= np.isfinite(np.ma.getdata(arr))
        vals = np.ma.getdata(arr)[valid]
        integer = np.issubdtype(arr.dtype, np.integer)
        out_sum = vals.astype(np.int64 if integer else np.float64).sum() if vals.size else None
        meta = {
            "format": fmt,
            "driver_layout": check.tags(ns="IMAGE_STRUCTURE").get("LAYOUT", "GTiff"),
            "width": check.width,
            "height": check.height,
            "crs": f"EPSG:{check.crs.to_epsg()}" if check.crs.to_epsg() else check.crs.to_wkt(),
            "transform_gdal": _gdal_transform(check.transform),
            "bounds": list(check.bounds),
            "nodata": check.nodata,
            "dtype": check.dtypes[0],
            "mask": "nodata_value" if check.nodata is not None else "internal_mask_band",
            "count_valid": int(valid.sum()),
            "sum_valid": _as_number(out_sum, bool(integer)),
            "resampled": resampled,
            "resampling": "nearest" if resampled else "none",
            "size_bytes": out_path.stat().st_size,
        }
    meta["warnings"] = warnings
    return meta


def monotonic_deadline(seconds: float) -> Deadline:
    end = time.monotonic() + seconds

    def check(stage: str) -> None:
        if time.monotonic() > end:
            raise ApiError(
                504,
                errors.JOB_TIMEOUT,
                f"the operation exceeded the {seconds:g}s time limit (stage: {stage})",
            )

    return check
