# Bounded-memory harmonisation helpers; every function works in windows, none reads a full raster.
# Policy: counts are only summed or left native; m2_per_cell areas are warped as area fractions;
# categorical classes use nearest neighbour only.
from __future__ import annotations

import math
from typing import Iterator

import numpy as np
import rasterio
from pyproj import Geod
from rasterio.enums import Resampling
from rasterio.transform import Affine, array_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

GTIFF_OPTS = dict(driver="GTiff", tiled=True, blockxsize=512, blockysize=512, compress="deflate", BIGTIFF="IF_SAFER")
_GEOD = Geod(ellps="WGS84")


# Yields block windows covering a width x height raster; used by every windowed reader here and in validate.py.
def iter_windows(width: int, height: int, size: int = 1024) -> Iterator[Window]:
    for r in range(0, height, size):
        for c in range(0, width, size):
            yield Window(c, r, min(size, width - c), min(size, height - r))


# True where a cell is finite and not the declared NoData (NaN NoData handled); used across the package.
def valid_mask(arr: np.ndarray, nodata) -> np.ndarray:
    m = np.ones(arr.shape, bool)
    if arr.dtype.kind == "f":
        m &= np.isfinite(arr)
    if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
        m &= arr != nodata
    return m


# Expands a fractional window to whole pixels; used by crop_to_bbox and warp_area_quantity.
def snap_window_outward(win: Window) -> Window:
    c0, r0 = math.floor(win.col_off + 1e-9), math.floor(win.row_off + 1e-9)
    c1, r1 = math.ceil(win.col_off + win.width - 1e-9), math.ceil(win.row_off + win.height - 1e-9)
    return Window(c0, r0, c1 - c0, r1 - r0)


# Whole-pixel (col, row) offset of `other`'s origin from `ref`'s; raises if not on one lattice.
# Used to register WorldPop years and GHSL tiles without resampling (sample, national, validate).
def lattice_offset(ref: Affine, other: Affine, tol: float = 1e-3) -> tuple[int, int]:
    if abs(ref.a - other.a) > 1e-9 or abs(ref.e - other.e) > 1e-9:
        raise ValueError(f"pixel size differs: {ref.a},{ref.e} vs {other.a},{other.e}")
    dc, dr = (other.c - ref.c) / ref.a, (other.f - ref.f) / ref.e
    if abs(dc - round(dc)) > tol or abs(dr - round(dr)) > tol:
        raise ValueError(f"grids are not on one lattice (fractional offset {dc:.4f},{dr:.4f} px)")
    return int(round(dc)), int(round(dr))


# Copies the part of a same-lattice source covering a reference window with NO resampling;
# uncovered cells become NoData. Used by sample.build_sample for GHSL tiles.
def extract_aligned_window(src_path: str, dst_path: str, ref: Affine, width: int, height: int,
                           block: int = 1024) -> dict:
    with rasterio.open(src_path) as src:
        dc, dr = lattice_offset(ref, src.transform)            # src origin relative to ref origin
        # A source with NO declared NoData (GHSL 4326_3ss) must not default to 0: zero means 'no built-up area'.
        # Use a sentinel outside the physical range instead (dtype max, e.g. 65535 for uint16) and document it.
        if src.nodata is not None:
            nd = src.nodata
        else:
            dt = np.dtype(src.dtypes[0])
            nd = np.iinfo(dt).max if dt.kind in "ui" else -99999.0
        prof = dict(src.profile, **GTIFF_OPTS, width=width, height=height, transform=ref, count=1, nodata=nd)
        covered = 0
        with rasterio.open(dst_path, "w", **prof) as dst:
            for w in iter_windows(width, height, block):
                out = np.full((w.height, w.width), nd, dtype=src.dtypes[0])
                # ref window -> src pixel coordinates
                c0, r0 = w.col_off - dc, w.row_off - dr
                sc0, sr0 = max(c0, 0), max(r0, 0)
                sc1, sr1 = min(c0 + w.width, src.width), min(r0 + w.height, src.height)
                if sc1 > sc0 and sr1 > sr0:
                    a = src.read(1, window=Window(sc0, sr0, sc1 - sc0, sr1 - sr0))
                    out[sr0 - r0:sr1 - r0, sc0 - c0:sc1 - c0] = a
                    covered += a.size
                dst.write(out, 1, window=w)
    return {"offset_cols": dc, "offset_rows": dr, "covered_cells": covered, "window_cells": width * height}


# Geodesic area (m2) of each pixel row of a north-up EPSG:4326 grid; used by sample.py and warp_area_quantity callers.
def cell_area_m2_rows(transform: Affine, height: int, row0: int = 0, width: int = 1) -> np.ndarray:
    dx, dy = transform.a, -transform.e
    out = np.empty(height)
    for i in range(height):
        lat_top = transform.f + (row0 + i) * transform.e
        lat_bot = lat_top - dy
        lon0, lon1 = transform.c, transform.c + dx
        area, _ = _GEOD.polygon_area_perimeter([lon0, lon1, lon1, lon0], [lat_top, lat_top, lat_bot, lat_bot])
        out[i] = abs(area)
    return out


# Windowed float64 sum over valid cells -> (sum, valid, nodata); used by sample.build_sample.
def raster_sum(path: str, band: int = 1, block: int = 1024) -> tuple[float, int, int]:
    total, n_valid, n_nd = 0.0, 0, 0
    with rasterio.open(path) as ds:
        nd = ds.nodata
        for w in iter_windows(ds.width, ds.height, block):
            a = ds.read(band, window=w)
            m = valid_mask(a, nd)
            total += float(a[m].sum(dtype=np.float64))
            n_valid += int(m.sum())
            n_nd += int(m.size - m.sum())
    return total, n_valid, n_nd


# Windowed crop to a lon/lat bbox snapped outward to source pixels, values untouched; used by sample.build_sample.
def crop_to_bbox(src_path: str, dst_path: str, bbox_wgs84: tuple[float, float, float, float],
                 band: int = 1, block: int = 1024) -> dict:
    with rasterio.open(src_path) as src:
        l, b, r, t = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84, densify_pts=21)
        win = snap_window_outward(from_bounds(l, b, r, t, src.transform))
        try:
            win = win.intersection(Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            raise ValueError("bbox does not intersect source raster") from None
        tr = src.window_transform(win)
        prof = dict(src.profile, **GTIFF_OPTS, width=int(win.width), height=int(win.height), transform=tr, count=1)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for w in iter_windows(int(win.width), int(win.height), block):
                rw = Window(win.col_off + w.col_off, win.row_off + w.row_off, w.width, w.height)
                dst.write(src.read(band, window=rw), 1, window=w)
            dst.update_tags(**src.tags())
        return {"width": int(win.width), "height": int(win.height), "transform": tuple(tr)[:6], "crs": str(src.crs)}


# Sum of valid source cells whose centre falls inside a target grid; used by warp_area_quantity.
def source_sum_within_grid(src_path: str, dst_crs, dst_transform: Affine, dst_width: int, dst_height: int,
                           block: int = 1024) -> float:
    from pyproj import Transformer
    dst_crs = rasterio.crs.CRS.from_user_input(dst_crs)
    x0, y0 = dst_transform.c, dst_transform.f
    x1, y1 = x0 + dst_width * dst_transform.a, y0 + dst_height * dst_transform.e
    total = 0.0
    with rasterio.open(src_path) as src:
        tr = Transformer.from_crs(src.crs, dst_crs, always_xy=True)
        for w in iter_windows(src.width, src.height, block):
            a = src.read(1, window=w)
            m = valid_mask(a, src.nodata)
            if not m.any():
                continue
            rows, cols = np.mgrid[w.row_off:w.row_off + w.height, w.col_off:w.col_off + w.width]
            xs, ys = rasterio.transform.xy(src.transform, rows + 0.0, cols + 0.0, offset="center")
            X, Y = tr.transform(np.asarray(xs).reshape(rows.shape), np.asarray(ys).reshape(rows.shape))
            inside = (X >= x0) & (X < x1) & (Y <= y0) & (Y > y1)
            total += float(a[m & inside].sum(dtype=np.float64))
    return total


# Area-conserving warp of an m2_per_cell raster to another grid (fraction -> average -> x target cell area).
# Mollweide fallback path; reports input/output sums. Used only when 4326_3ss is unavailable (tested).
def warp_area_quantity(src_path: str, dst_path: str, dst_crs, dst_transform: Affine, dst_width: int, dst_height: int,
                       src_cell_area_m2: float, dst_cell_area_fn=None, *, block: int = 512,
                       dst_nodata: float = -1.0) -> dict:
    dst_crs = rasterio.crs.CRS.from_user_input(dst_crs)
    in_sum = out_sum = 0.0
    with rasterio.open(src_path) as src:
        nd = src.nodata
        prof = dict(driver="GTiff", tiled=True, blockxsize=block, blockysize=block, compress="deflate",
                    dtype="float32", count=1, crs=dst_crs, transform=dst_transform, width=dst_width,
                    height=dst_height, nodata=dst_nodata, BIGTIFF="IF_SAFER")
        with rasterio.open(dst_path, "w", **prof) as dst:
            for w in iter_windows(dst_width, dst_height, block):
                wt = rasterio.windows.transform(w, dst_transform)
                bounds = array_bounds(w.height, w.width, wt)
                sl, sb, sr, st = transform_bounds(dst_crs, src.crs, *bounds, densify_pts=21)
                pad = 2 * max(abs(src.res[0]), abs(src.res[1]))
                sw = snap_window_outward(from_bounds(sl - pad, sb - pad, sr + pad, st + pad, src.transform))
                try:
                    sw = sw.intersection(Window(0, 0, src.width, src.height))
                except rasterio.errors.WindowError:
                    sw = None
                if sw is None or sw.width <= 0 or sw.height <= 0:
                    dst.write(np.full((w.height, w.width), dst_nodata, "float32"), 1, window=w)
                    continue
                a = src.read(1, window=sw).astype("float64")
                m = valid_mask(a, nd)
                frac = np.where(m, a / src_cell_area_m2, 0.0)
                cover = m.astype("float64")
                st_ = src.window_transform(sw)
                out_f = np.zeros((w.height, w.width)); out_c = np.zeros((w.height, w.width))
                reproject(frac, out_f, src_transform=st_, src_crs=src.crs, dst_transform=wt, dst_crs=dst_crs,
                          resampling=Resampling.average)
                reproject(cover, out_c, src_transform=st_, src_crs=src.crs, dst_transform=wt, dst_crs=dst_crs,
                          resampling=Resampling.average)
                if dst_cell_area_fn is not None:
                    areas = dst_cell_area_fn(wt, w.height, 0)[:, None]
                else:
                    areas = abs(dst_transform.a * dst_transform.e)
                out = np.where(out_c > 0, out_f / np.where(out_c > 0, out_c, 1) * areas, dst_nodata)
                out_sum += float(out[out_c > 0].sum())
                dst.write(out.astype("float32"), 1, window=w)
    in_sum = source_sum_within_grid(src_path, dst_crs, dst_transform, dst_width, dst_height)
    rel = (out_sum - in_sum) / in_sum if in_sum else float("nan")
    return {"input_sum_m2": in_sum, "output_sum_m2": out_sum, "relative_diff": rel}


# Nearest-neighbour warp of a categorical raster (class codes never interpolated); LULC fallback path (tested).
def warp_categorical(src_path: str, dst_path: str, dst_crs, dst_transform: Affine, dst_width: int, dst_height: int,
                     *, block: int = 512, dst_nodata: int = 0) -> None:
    dst_crs = rasterio.crs.CRS.from_user_input(dst_crs)
    with rasterio.open(src_path) as src:
        prof = dict(driver="GTiff", tiled=True, blockxsize=block, blockysize=block, compress="deflate",
                    dtype=src.dtypes[0], count=1, crs=dst_crs, transform=dst_transform, width=dst_width,
                    height=dst_height, nodata=dst_nodata if src.nodata is None else src.nodata)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for w in iter_windows(dst_width, dst_height, block):
                wt = rasterio.windows.transform(w, dst_transform)
                out = np.full((w.height, w.width), prof["nodata"], dtype=src.dtypes[0])
                reproject(rasterio.band(src, 1), out, dst_transform=wt, dst_crs=dst_crs,
                          resampling=Resampling.nearest, dst_nodata=prof["nodata"])
                dst.write(out, 1, window=w)


# Count-preserving aggregation by an integer factor (sum, never mean); helper for count rasters (tested).
def block_sum(src_path: str, dst_path: str, factor: int) -> dict:
    with rasterio.open(src_path) as src:
        nd = src.nodata
        h, w = src.height // factor * factor, src.width // factor * factor
        prof = dict(src.profile, **GTIFF_OPTS, width=w // factor, height=h // factor,
                    transform=src.transform * Affine.scale(factor), dtype="float32", nodata=nd)
        in_sum = out_sum = 0.0
        step = factor * max(1, 1024 // factor)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for win in iter_windows(w, h, step):
                a = src.read(1, window=win).astype("float64")
                m = valid_mask(a, nd)
                in_sum += float(a[m].sum())
                s = np.where(m, a, 0).reshape(win.height // factor, factor, win.width // factor, factor).sum((1, 3))
                anyv = m.reshape(win.height // factor, factor, win.width // factor, factor).any((1, 3))
                s = np.where(anyv, s, nd)
                out_sum += float(s[anyv].sum())
                dst.write(s.astype("float32"), 1, window=Window(win.col_off // factor, win.row_off // factor,
                                                                win.width // factor, win.height // factor))
    return {"input_sum": in_sum, "output_sum": out_sum, "abs_diff": abs(out_sum - in_sum)}
