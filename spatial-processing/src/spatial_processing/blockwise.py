"""Bounded-memory processing for AOIs too large to load at once (completion P2/P3).

The in-memory path (:func:`spatial_processing.raster.select_pixels`) reads the whole candidate
window; it is limited to ``MAX_WINDOW_PIXELS`` (25 M pixels, ~100 MB of float32). A national
3 arc-second India raster has 1.21e9 pixels, so state- or country-sized AOIs are processed here,
one ``block_size x block_size`` window at a time:

* same analytical rule as the in-memory path: a pixel is in the AOI iff its CENTER is inside
  (``geometry_mask(..., all_touched=False)`` evaluated on each block's own transform, which is
  exactly the full-window mask restricted to that block), NoData / masked / non-finite pixels
  are excluded, values are read at native resolution in the native CRS;
* blocks whose extent does not intersect the AOI are skipped without reading;
* sums are accumulated per block in float64 (int64 for integer rasters) and combined with
  ``math.fsum``, so results match the in-memory path to floating-point rounding;
* peak memory is O(block_size^2), independent of the AOI size;
* ``progress(fraction, message)`` is called after every block; it may raise (cancellation,
  time limit), which aborts cleanly.
"""

from __future__ import annotations

import math
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import rasterio
import rasterio.crs
import rasterio.shutil
import rasterio.windows
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from shapely import prepared
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry

from . import errors
from .errors import ApiError
from .raster import _as_number, _gdal_transform, _predictor, candidate_window, summarize_statistics
from .units import UnitSemantics

Progress = Callable[[float, str], None]


def _no_progress(fraction: float, message: str) -> None:
    return None


def iter_blocks(window: Window, block: int) -> Iterator[Window]:
    """Row-major tiling of ``window`` into windows of at most ``block x block`` pixels."""
    c0, r0 = int(window.col_off), int(window.row_off)
    w, h = int(window.width), int(window.height)
    for r in range(r0, r0 + h, block):
        for c in range(c0, c0 + w, block):
            yield Window(c, r, min(block, c0 + w - c), min(block, r0 + h - r))


def _block_mask(src, geom_json, win: Window) -> np.ndarray:
    return geometry_mask([geom_json], out_shape=(int(win.height), int(win.width)),
                         transform=src.window_transform(win), all_touched=False, invert=True)


def _invalid(data: np.ma.MaskedArray, src_nodata, catalog_nodata) -> tuple[np.ndarray, np.ndarray]:
    values = np.ma.getdata(data)
    invalid = np.ma.getmaskarray(data).copy()
    if np.issubdtype(values.dtype, np.floating):
        invalid |= ~np.isfinite(values)
    if catalog_nodata is not None and src_nodata != catalog_nodata:
        invalid |= values == np.asarray(catalog_nodata).astype(values.dtype)
    return values, invalid


def _blocks_touching(src, aoi_native: BaseGeometry, window: Window, block: int) -> list[Window]:
    aoi = prepared.prep(aoi_native)
    out = []
    for win in iter_blocks(window, block):
        left, bottom, right, top = rasterio.windows.bounds(win, src.transform)
        if aoi.intersects(box(left, bottom, right, top)):
            out.append(win)
    return out


def window_pixels(src, aoi_native: BaseGeometry) -> int:
    win = candidate_window(src, aoi_native)
    return int(win.width) * int(win.height)


def zonal_blockwise(src, aoi_native: BaseGeometry, *, semantics: UnitSemantics, operations: list[str],
                    catalog_nodata: float | None, block: int, progress: Progress = _no_progress) -> dict[str, Any]:
    window = candidate_window(src, aoi_native)
    geom_json = mapping(aoi_native)
    blocks = _blocks_touching(src, aoi_native, window, block)
    integer = bool(np.issubdtype(np.dtype(src.dtypes[0]), np.integer))
    count_valid = pixels_in_aoi = 0
    partial_sums: list[float] = []
    int_total = 0
    for i, win in enumerate(blocks):
        inside = _block_mask(src, geom_json, win)
        n_inside = int(inside.sum())
        if n_inside:
            values, invalid = _invalid(src.read(1, window=win, masked=True), src.nodata, catalog_nodata)
            valid = inside & ~invalid
            n_valid = int(valid.sum())
            pixels_in_aoi += n_inside
            count_valid += n_valid
            if n_valid:
                if integer:
                    int_total += int(values[valid].astype(np.int64).sum())
                else:
                    partial_sums.append(float(values[valid].astype(np.float64).sum()))
        progress((i + 1) / max(1, len(blocks)), f"block {i + 1}/{len(blocks)}")
    if pixels_in_aoi == 0:
        raise ApiError(422, errors.NO_PIXELS_SELECTED,
                       "the AOI overlaps the raster but contains no pixel centers (pixel-center rule); "
                       "use a larger AOI")
    total = int_total if integer else math.fsum(partial_sums)
    mean = (total / count_valid) if count_valid else None
    stats = summarize_statistics(count_valid, pixels_in_aoi, total if count_valid else None, mean, integer,
                                 semantics, operations)
    stats["processing"] = {"path": "blockwise", "block_size": block, "blocks_read": len(blocks),
                           "candidate_window_pixels": int(window.width) * int(window.height)}
    return stats


def _selected_extent(src, geom_json, blocks: list[Window], progress: Progress) -> Window | None:
    r0 = c0 = math.inf
    r1 = c1 = -math.inf
    for i, win in enumerate(blocks):
        inside = _block_mask(src, geom_json, win)
        if inside.any():
            rows = np.flatnonzero(inside.any(axis=1))
            cols = np.flatnonzero(inside.any(axis=0))
            r0 = min(r0, int(win.row_off) + int(rows[0]))
            r1 = max(r1, int(win.row_off) + int(rows[-1]) + 1)
            c0 = min(c0, int(win.col_off) + int(cols[0]))
            c1 = max(c1, int(win.col_off) + int(cols[-1]) + 1)
        progress(0.25 * (i + 1) / max(1, len(blocks)), "locating selected pixels")
    if r0 is math.inf:
        return None
    return Window(c0, r0, c1 - c0, r1 - r0)


def clip_blockwise(src, aoi_native: BaseGeometry, out_path: Path, *, fmt: str, output_crs: CRS | None,
                   tags: dict[str, str], units: str | None, catalog_nodata: float | None, block: int,
                   max_output_bytes: int, progress: Progress = _no_progress) -> dict[str, Any]:
    """Write the AOI's pixels (others -> NoData/mask) to ``out_path`` without loading the window."""
    window = candidate_window(src, aoi_native)
    geom_json = mapping(aoi_native)
    sel = _selected_extent(src, geom_json, _blocks_touching(src, aoi_native, window, block), progress)
    if sel is None:
        raise ApiError(422, errors.NO_PIXELS_SELECTED,
                       "the AOI overlaps the raster but contains no pixel centers (pixel-center rule); "
                       "use a larger AOI")
    dtype = src.dtypes[0]
    width, height = int(sel.width), int(sel.height)
    if width * height * np.dtype(dtype).itemsize > max_output_bytes:
        raise ApiError(413, errors.OUTPUT_TOO_LARGE,
                       f"the clipped output would exceed {max_output_bytes} bytes",
                       {"width": width, "height": height})
    nodata = src.nodata if src.nodata is not None else catalog_nodata
    src_crs = CRS.from_user_input(src.crs)
    reproject = output_crs is not None and not output_crs.equals(src_crs, ignore_axis_order=True)
    if reproject and nodata is None:
        raise ApiError(422, errors.UNSUPPORTED_OPERATION,
                       "reprojecting a large clip needs a raster NoData value; request the native CRS")

    out_dir = out_path.parent
    tmp_native = out_dir / f".tmp-{uuid.uuid4().hex}.tif"
    tmp_warp = out_dir / f".tmp-{uuid.uuid4().hex}.warp.tif"
    tmp_cog = out_dir / f".tmp-{uuid.uuid4().hex}.cog.tif"
    profile = {
        "driver": "GTiff", "dtype": dtype, "count": 1, "width": width, "height": height,
        "crs": src.crs, "transform": src.window_transform(sel), "nodata": nodata,
        "compress": "DEFLATE", "predictor": _predictor(dtype), "tiled": True,
        "blockxsize": 256, "blockysize": 256, "BIGTIFF": "IF_SAFER",
    }
    count_valid = pixels_in_aoi = 0
    try:
        with rasterio.open(tmp_native, "w", **profile) as dst:
            blocks = list(iter_blocks(sel, block))
            for i, win in enumerate(blocks):
                inside = _block_mask(src, geom_json, win)
                out_win = Window(int(win.col_off) - int(sel.col_off), int(win.row_off) - int(sel.row_off),
                                 int(win.width), int(win.height))
                if inside.any():
                    values, invalid = _invalid(src.read(1, window=win, masked=True), src.nodata, catalog_nodata)
                    keep = inside & ~invalid
                    data = values.copy()
                else:  # block inside the clip rectangle but outside the AOI: no read needed
                    keep = inside
                    data = np.zeros(inside.shape, dtype=dtype)
                pixels_in_aoi += int(inside.sum())
                count_valid += int(keep.sum())
                if nodata is not None:
                    data[~keep] = np.asarray(nodata).astype(data.dtype)
                dst.write(data, 1, window=out_win)
                if nodata is None:
                    dst.write_mask(np.where(keep, 255, 0).astype(np.uint8), window=out_win)
                progress(0.25 + 0.5 * (i + 1) / max(1, len(blocks)), f"writing block {i + 1}/{len(blocks)}")
            dst.update_tags(**tags)
            dst.set_band_description(1, src.descriptions[0] or tags.get("DATASET_ID", ""))
            if units:
                dst.set_band_unit(1, units)

        current = tmp_native
        warnings: list[str] = []
        if reproject:
            with rasterio.open(tmp_native) as native, WarpedVRT(
                    native, crs=rasterio.crs.CRS.from_wkt(output_crs.to_wkt()), resampling=Resampling.nearest,
                    src_nodata=nodata, nodata=nodata, warp_mem_limit=256) as vrt:
                rasterio.shutil.copy(vrt, tmp_warp, driver="GTiff", COMPRESS="DEFLATE", PREDICTOR=_predictor(dtype),
                                     TILED="YES", BLOCKXSIZE=256, BLOCKYSIZE=256, BIGTIFF="IF_SAFER")
            with rasterio.open(tmp_warp, "r+") as warped:
                warped.update_tags(**tags)
                if units:
                    warped.set_band_unit(1, units)
            current = tmp_warp
            warnings.append(
                "Output was reprojected to the requested output_crs with nearest-neighbour resampling onto a "
                "new grid; cell values are copied, not area-weighted, so totals of count rasters are NOT "
                "preserved. Use the native CRS (omit output_crs) for analytical exports.")
        progress(0.85, "finalising file")
        if fmt == "COG":
            rasterio.shutil.copy(current, tmp_cog, driver="COG", COMPRESS="DEFLATE", PREDICTOR="YES",
                                 BLOCKSIZE=256, OVERVIEWS="NONE", BIGTIFF="IF_SAFER")
            os.replace(tmp_cog, out_path)
        else:
            os.replace(current, out_path)
    finally:
        for leftover in (tmp_native, tmp_warp, tmp_cog):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass

    meta = _verify_blockwise(out_path, fmt, reproject)
    progress(1.0, "done")
    meta["warnings"] = warnings
    return {"output": meta, "count_valid": count_valid, "pixels_in_aoi": pixels_in_aoi}


def _verify_blockwise(path: Path, fmt: str, resampled: bool) -> dict[str, Any]:
    """Re-open the written file and recompute count/sum block by block (bounded memory)."""
    with rasterio.open(path) as check:
        integer = bool(np.issubdtype(np.dtype(check.dtypes[0]), np.integer))
        n_valid = 0
        int_total = 0
        partial: list[float] = []
        for _, win in check.block_windows(1):
            arr = check.read(1, window=win, masked=True)
            valid = ~np.ma.getmaskarray(arr)
            data = np.ma.getdata(arr)
            if not integer:
                valid &= np.isfinite(data)
            n_valid += int(valid.sum())
            if integer:
                int_total += int(data[valid].astype(np.int64).sum())
            else:
                partial.append(float(data[valid].astype(np.float64).sum()))
        total = int_total if integer else math.fsum(partial)
        return {
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
            "count_valid": n_valid,
            "sum_valid": _as_number(total if n_valid else None, integer),
            "resampled": resampled,
            "resampling": "nearest" if resampled else "none",
            "size_bytes": path.stat().st_size,
        }
