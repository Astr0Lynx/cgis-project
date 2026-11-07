"""Target grid, processing extent, deterministic tiling and source-grid mapping."""

from __future__ import annotations

import glob
import math
import os
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import rasterio
from affine import Affine
from rasterio.windows import Window

from . import core

# Tolerance (in source pixels) for treating two grids as having identical cell size.
SAME_RES_TOL_PX = 0.01
# Sub-pixel offsets smaller than this (in pixels) are snapped to exact alignment.
SNAP_PX = 1e-3


def replace_retry(src, dst, attempts: int = 8) -> None:
    """``os.replace`` with backoff: on Windows, antivirus/indexer can briefly lock a just-written file
    (WinError 5/32). The rename stays atomic; only transient PermissionErrors are retried."""
    import time
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.1 * 2 ** i)


def ensure_proj_data() -> None:
    """Point PROJ at rasterio's bundled database (avoid foreign proj.db, e.g. from a PostGIS install)."""
    pd = Path(rasterio.__file__).parent / "proj_data"
    if pd.is_dir():
        os.environ["PROJ_DATA"] = str(pd)
        os.environ.pop("PROJ_LIB", None)
        try:
            from rasterio._env import set_proj_data_search_path
            set_proj_data_search_path(str(pd))
        except ImportError:  # pragma: no cover
            pass


@dataclass(frozen=True)
class TargetGrid:
    """The WorldPop native grid (counts are never resampled) and the processing extent within it."""

    crs_wkt: str
    epsg: int | None
    full_transform: Affine
    full_width: int
    full_height: int
    row0: int          # extent origin in the full grid (multiple of the parent block)
    col0: int
    height: int
    width: int

    @property
    def transform(self) -> Affine:
        return self.full_transform * Affine.translation(self.col0, self.row0)

    @property
    def res(self) -> tuple[float, float]:
        return self.full_transform.a, -self.full_transform.e

    def signature(self) -> dict:
        return {"crs_epsg": self.epsg, "transform": list(self.full_transform)[:6],
                "width": self.full_width, "height": self.full_height}

    def tiles(self, tile: int, tile_cols: int | None = None) -> list[tuple[int, int, int, int]]:
        """Deterministic row-major tiles ``(r, c, h, w)`` in extent coordinates.

        ``tile_cols`` >= width gives full-width row bands (efficient for strip-organised inputs such as
        the WorldPop GeoTIFFs, which store one row per strip).
        """
        tc = tile_cols or tile
        out = []
        for r in range(0, self.height, tile):
            for c in range(0, self.width, tc):
                out.append((r, c, min(tile, self.height - r), min(tc, self.width - c)))
        return out


def grid_from_raster(path: Path, block: int, bbox: tuple[float, float, float, float] | None = None
                     ) -> TargetGrid:
    with rasterio.open(path) as ds:
        t = ds.transform
        if t.b != 0 or t.d != 0 or t.e >= 0:
            raise ValueError(f"{path.name}: target grid must be north-up without rotation")
        epsg = ds.crs.to_epsg() if ds.crs else None
        if ds.crs is None:
            raise ValueError(f"{path.name}: missing CRS")
        W, H = ds.width, ds.height
        r0, c0, h, w = 0, 0, H, W
        if bbox is not None:
            inv = ~t
            ca, ra = inv * (bbox[0], bbox[3])
            cb, rb = inv * (bbox[2], bbox[1])
            # snap outward to the parent-block lattice anchored at the raster origin
            c0 = max(0, int(math.floor(ca / block)) * block)
            r0 = max(0, int(math.floor(ra / block)) * block)
            c1 = min(W, int(math.ceil(cb / block)) * block)
            r1 = min(H, int(math.ceil(rb / block)) * block)
            if c1 <= c0 or r1 <= r0:
                raise ValueError("bbox does not intersect the WorldPop grid")
            h, w = r1 - r0, c1 - c0
        return TargetGrid(ds.crs.to_wkt(), epsg, t, W, H, r0, c0, h, w)


# --------------------------------------------------------------------------------------------------
# GHSL tile mosaic
# --------------------------------------------------------------------------------------------------
def build_mosaic_vrt(pattern: str, vrt_path: Path) -> Path:
    """Write a VRT mosaicking GHSL tiles that lie on one global lattice. Absent (all-ocean) tiles read as 0."""
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"no GHSL tiles match {pattern}")
    metas = []
    for f in files:
        with rasterio.open(f) as ds:
            if ds.transform.b or ds.transform.d:
                raise ValueError(f"{f}: rotated GHSL tile")
            metas.append((f, ds.transform, ds.width, ds.height, ds.dtypes[0], ds.crs.to_wkt(), ds.nodata))
    t0 = metas[0][1]
    res_x, res_y = t0.a, -t0.e
    if any(abs(m[1].a - res_x) / res_x > 1e-9 or m[5] != metas[0][5] or m[4] != metas[0][4] for m in metas):
        raise ValueError(f"GHSL tiles for {pattern} do not share resolution/CRS/dtype")
    x0 = min(m[1].c for m in metas)
    y0 = max(m[1].f for m in metas)
    x1 = max(m[1].c + m[2] * res_x for m in metas)
    y1 = min(m[1].f - m[3] * res_y for m in metas)
    W = int(round((x1 - x0) / res_x))
    H = int(round((y0 - y1) / res_y))
    dtype = {"uint16": "UInt16", "uint8": "Byte", "int16": "Int16", "int32": "Int32", "uint32": "UInt32",
             "float32": "Float32", "float64": "Float64"}[metas[0][4]]
    nod = metas[0][6]
    parts = [f'<VRTDataset rasterXSize="{W}" rasterYSize="{H}">',
             f"  <SRS>{escape(metas[0][5])}</SRS>",
             f"  <GeoTransform>{x0!r}, {res_x!r}, 0.0, {y0!r}, 0.0, {-res_y!r}</GeoTransform>",
             f'  <VRTRasterBand dataType="{dtype}" band="1">']
    if nod is not None:
        parts.append(f"    <NoDataValue>{nod!r}</NoDataValue>")
    for f, t, w, h, *_ in metas:
        xo = int(round((t.c - x0) / res_x))
        yo = int(round((y0 - t.f) / res_y))
        parts.append(f'    <SimpleSource><SourceFilename relativeToVRT="0">{escape(str(Path(f).resolve()))}'
                     f'</SourceFilename><SourceBand>1</SourceBand>'
                     f'<SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>'
                     f'<DstRect xOff="{xo}" yOff="{yo}" xSize="{w}" ySize="{h}"/></SimpleSource>')
    parts += ["  </VRTRasterBand>", "</VRTDataset>"]
    vrt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = vrt_path.with_suffix(".tmp")
    tmp.write_text("\n".join(parts), encoding="utf-8")
    replace_retry(tmp, vrt_path)
    return vrt_path


@dataclass(frozen=True)
class SourceMapping:
    """Global (tile-independent) mapping from the target grid to a same-resolution source grid.

    Target full-grid cell (R, C) covers source pixel coordinates ``[C + iu + fx, ... + 1]`` x
    ``[R + iv + fy, ... + 1]``. Computing ``(iu, fx, iv, fy)`` once for the whole grid makes every tile see
    bit-identical weights, regardless of tile size.
    """

    iu: int
    fx: float
    iv: int
    fy: float
    same_res: bool


def source_mapping(target: TargetGrid, src_transform: Affine, src_shape: tuple[int, int]) -> SourceMapping:
    tt = target.full_transform
    tx, ty = tt.a, -tt.e
    sx, sy = src_transform.a, -src_transform.e
    max_px = max(target.full_width, target.full_height, *src_shape)
    same = abs(tx - sx) / sx * max_px < SAME_RES_TOL_PX and abs(ty - sy) / sy * max_px < SAME_RES_TOL_PX
    u0 = (tt.c - src_transform.c) / sx
    v0 = (src_transform.f - tt.f) / sy

    def split(x: float) -> tuple[int, float]:
        i = math.floor(x)
        f = x - i
        if f < SNAP_PX:
            f = 0.0
        elif f > 1 - SNAP_PX:
            i, f = i + 1, 0.0
        return i, f

    iu, fx = split(u0)
    iv, fy = split(v0)
    return SourceMapping(iu, fx, iv, fy, same)


class WeightSource:
    """Reads a GHSL mosaic as an extensive per-cell quantity on target windows."""

    def __init__(self, vrt: Path, target: TargetGrid, nodata_override: float | None = None):
        self.ds = rasterio.open(vrt)
        self.target = target
        self.nodata = self.ds.nodata if self.ds.nodata is not None else nodata_override
        self.map = source_mapping(target, self.ds.transform, (self.ds.height, self.ds.width))

    def close(self) -> None:
        self.ds.close()

    def read(self, r: int, c: int, h: int, w: int) -> tuple[np.ndarray, int]:
        """Return ``(values float64 (h, w), n_outside)`` for extent-window (r, c, h, w).

        Cells outside the mosaic footprint read as 0 (GHSL tiles are absent only over open ocean) and are
        counted in ``n_outside`` for coverage diagnostics.
        """
        R = self.target.row0 + r
        C = self.target.col0 + c
        m = self.map
        if not m.same_res:
            return self._read_general(r, c, h, w)
        win = Window(C + m.iu, R + m.iv, w + 1, h + 1)
        raw = self.ds.read(1, window=win, boundless=True, fill_value=0, masked=False).astype(np.float64)
        inside = np.zeros_like(raw, dtype=bool)
        rr0, cc0 = max(0, -win.row_off), max(0, -win.col_off)
        rr1 = min(win.height, self.ds.height - win.row_off)
        cc1 = min(win.width, self.ds.width - win.col_off)
        if rr1 > rr0 and cc1 > cc0:
            inside[rr0:rr1, cc0:cc1] = True
        if self.nodata is not None:
            raw[(raw == self.nodata) & inside] = np.nan
        vals = core.shift_weights(raw, m.fx, m.fy)
        n_out = int((~inside[:h, :w]).sum())
        return vals, n_out

    def _read_general(self, r: int, c: int, h: int, w: int) -> tuple[np.ndarray, int]:
        """Different cell sizes / CRS: GDAL area-weighted ``sum`` warp (extensive quantity)."""
        from rasterio.warp import Resampling, reproject

        dst = np.zeros((h, w), dtype=np.float64)
        t = self.target.transform * Affine.translation(c, r)
        reproject(rasterio.band(self.ds, 1), dst, dst_transform=t, dst_crs=self.target.crs_wkt,
                  src_nodata=self.nodata, dst_nodata=np.nan, resampling=Resampling.sum, init_dest_nodata=False)
        return dst, 0
