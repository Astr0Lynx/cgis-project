# Bounded-memory zonal statistics and clipping for AOIs too large to load at once (async jobs).
# Same rule as raster.select_pixels (pixel center inside the AOI, NoData excluded, native grid), but
# processed one block_size x block_size window at a time: blocks that miss the AOI are skipped, sums
# are combined with math.fsum (int64 for integer rasters), so peak memory is O(block_size^2).

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
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from shapely import prepared
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry

from .config import ApiError
from .raster import (
    candidate_window,
    center_mask,
    describe_output,
    no_pixels_error,
    predictor,
    read_values,
    summarize_statistics,
)

# Progress callback: (fraction 0..1, message); may raise to abort (cancellation, time limit).
Progress = Callable[[float, str], None]


# Progress callback that does nothing. Default for direct (test) calls.
def _no_progress(fraction: float, message: str) -> None:
    return None


# Row-major tiling of `window` into windows of at most block x block pixels.
# Used by _blocks_touching and clip_blockwise.
def iter_blocks(window: Window, block: int) -> Iterator[Window]:
    c0, r0 = int(window.col_off), int(window.row_off)
    w, h = int(window.width), int(window.height)
    for r in range(r0, r0 + h, block):
        for c in range(c0, c0 + w, block):
            yield Window(c, r, min(block, c0 + w - c), min(block, r0 + h - r))


# The blocks of `window` whose extent intersects the AOI. Used by zonal_blockwise and clip_blockwise.
def _blocks_touching(src, aoi_native: BaseGeometry, window: Window, block: int) -> list[Window]:
    aoi = prepared.prep(aoi_native)
    return [win for win in iter_blocks(window, block)
            if aoi.intersects(box(*rasterio.windows.bounds(win, src.transform)))]


# Size of the AOI's candidate window in pixels (no pixel is read). Used by service._window_pixels.
def window_pixels(src, aoi_native: BaseGeometry) -> int:
    win = candidate_window(src, aoi_native)
    return int(win.width) * int(win.height)


# Zonal statistics block by block; same result shape as raster.zonal_statistics plus a "processing"
# entry. Used by service._zonal_work for async jobs.
def zonal_blockwise(src, aoi_native: BaseGeometry, *, units: str | None, operations: list[str],
                    catalog_nodata: float | None, block: int, progress: Progress = _no_progress) -> dict[str, Any]:
    window = candidate_window(src, aoi_native)
    geom_json = mapping(aoi_native)
    blocks = _blocks_touching(src, aoi_native, window, block)
    integer = bool(np.issubdtype(np.dtype(src.dtypes[0]), np.integer))
    count_valid = pixels_in_aoi = 0
    partial_sums: list[float] = []
    int_total = 0
    for i, win in enumerate(blocks):
        inside = center_mask(src, geom_json, win)
        n_inside = int(inside.sum())
        if n_inside:
            values, invalid = read_values(src, win, catalog_nodata)
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
        raise no_pixels_error()
    total = int_total if integer else math.fsum(partial_sums)
    mean = (total / count_valid) if count_valid else None
    stats = summarize_statistics(count_valid, pixels_in_aoi, total if count_valid else None, mean, units, operations)
    stats["processing"] = {"path": "blockwise", "block_size": block, "blocks_read": len(blocks),
                           "candidate_window_pixels": int(window.width) * int(window.height)}
    return stats


# The exact pixel extent of the AOI's selected pixels (None if there are none), found block by block.
# Used by clip_blockwise.
def _selected_extent(src, geom_json, blocks: list[Window], progress: Progress) -> Window | None:
    r0 = c0 = math.inf
    r1 = c1 = -math.inf
    for i, win in enumerate(blocks):
        inside = center_mask(src, geom_json, win)
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


# Write the AOI's pixels (others -> NoData or mask) to `out_path` as GTiff/COG without loading the
# window, optionally reprojecting (nearest neighbour, needs a NoData value); returns
# {"output": metadata, "count_valid", "pixels_in_aoi"}. Used by service._clip_work for async jobs.
def clip_blockwise(src, aoi_native: BaseGeometry, out_path: Path, *, fmt: str, output_crs: CRS | None,
                   tags: dict[str, str], units: str | None, catalog_nodata: float | None, block: int,
                   max_output_bytes: int, progress: Progress = _no_progress) -> dict[str, Any]:
    window = candidate_window(src, aoi_native)
    geom_json = mapping(aoi_native)
    sel = _selected_extent(src, geom_json, _blocks_touching(src, aoi_native, window, block), progress)
    if sel is None:
        raise no_pixels_error()
    dtype = src.dtypes[0]
    width, height = int(sel.width), int(sel.height)
    if width * height * np.dtype(dtype).itemsize > max_output_bytes:
        raise ApiError(413, "OUTPUT_TOO_LARGE", f"the clipped output would exceed {max_output_bytes} bytes",
                       {"width": width, "height": height})
    nodata = src.nodata if src.nodata is not None else catalog_nodata
    src_crs = CRS.from_user_input(src.crs)
    reproject = output_crs is not None and not output_crs.equals(src_crs, ignore_axis_order=True)
    if reproject and nodata is None:
        raise ApiError(422, "UNSUPPORTED_OPERATION",
                       "reprojecting a large clip needs a raster NoData value; request the native CRS")

    out_dir = out_path.parent
    tmp_native = out_dir / f".tmp-{uuid.uuid4().hex}.tif"
    tmp_warp = out_dir / f".tmp-{uuid.uuid4().hex}.warp.tif"
    tmp_cog = out_dir / f".tmp-{uuid.uuid4().hex}.cog.tif"
    profile = {
        "driver": "GTiff", "dtype": dtype, "count": 1, "width": width, "height": height,
        "crs": src.crs, "transform": src.window_transform(sel), "nodata": nodata,
        "compress": "DEFLATE", "predictor": predictor(dtype), "tiled": True,
        "blockxsize": 256, "blockysize": 256, "BIGTIFF": "IF_SAFER",
    }
    count_valid = pixels_in_aoi = 0
    warnings: list[str] = []
    try:
        with rasterio.open(tmp_native, "w", **profile) as dst:
            blocks = list(iter_blocks(sel, block))
            for i, win in enumerate(blocks):
                inside = center_mask(src, geom_json, win)
                out_win = Window(int(win.col_off) - int(sel.col_off), int(win.row_off) - int(sel.row_off),
                                 int(win.width), int(win.height))
                if inside.any():
                    values, invalid = read_values(src, win, catalog_nodata)
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
        if reproject:
            with rasterio.open(tmp_native) as native, WarpedVRT(
                    native, crs=rasterio.crs.CRS.from_wkt(output_crs.to_wkt()), resampling=Resampling.nearest,
                    src_nodata=nodata, nodata=nodata, warp_mem_limit=256) as vrt:
                rasterio.shutil.copy(vrt, tmp_warp, driver="GTiff", COMPRESS="DEFLATE", PREDICTOR=predictor(dtype),
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
            leftover.unlink(missing_ok=True)

    meta = _verify(out_path, fmt, reproject)
    progress(1.0, "done")
    meta["warnings"] = warnings
    return {"output": meta, "count_valid": count_valid, "pixels_in_aoi": pixels_in_aoi}


# Reopen the written clip and measure count/sum of its valid pixels block by block (bounded memory).
# Used by clip_blockwise.
def _verify(path: Path, fmt: str, resampled: bool) -> dict[str, Any]:
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
        return describe_output(check, path, fmt, resampled, n_valid, total if n_valid else None)
