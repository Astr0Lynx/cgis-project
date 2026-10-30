"""Test-only raster stubs that follow contracts/SYNTHETIC_DATA.md exactly.

Agent 3 owns the real fixture generator; this module only recreates the contract's
grid so the processing service can be tested in isolation. Values are SYNTHETIC.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer

NODATA = -9999.0

# Contract grid: 64x64, EPSG:4326, UL (78.000, 18.000), pixel (0.001, -0.001).
GRID_TRANSFORM = Affine(0.001, 0.0, 78.000, 0.0, -0.001, 18.000)
GRID_SIZE = 64
NODATA_STRIP = 4  # first 4 rows and first 4 columns are NoData


def rect(minx: float, miny: float, maxx: float, maxy: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
    }


TOWN_A = rect(78.010, 17.980, 78.020, 17.990)
TOWN_B = rect(78.030, 17.950, 78.035, 17.955)
# Upper-left 10x10 block: overlaps the 4-row/4-column NoData strip (64 NoData, 36 valid).
NODATA_CORNER = rect(78.000, 17.990, 78.010, 18.000)
DISJOINT = rect(79.000, 17.000, 79.010, 17.010)


def _write(path: Path, data: np.ndarray, transform: Affine, crs: str, nodata, **extra) -> Path:
    profile = {
        "driver": "GTiff",
        "dtype": str(data.dtype),
        "count": 1,
        "width": data.shape[1],
        "height": data.shape[0],
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "DEFLATE",
        **extra,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        dst.update_tags(SYNTHETIC="true", SOURCE="deterministic-fixture (test stub)")
    return path


def contract_raster(path: Path, value: float) -> Path:
    """synthetic_2001.tif (value 1) / synthetic_2011.tif (value 2) per the contract."""
    data = np.full((GRID_SIZE, GRID_SIZE), value, dtype=np.float32)
    data[:NODATA_STRIP, :] = NODATA
    data[:, :NODATA_STRIP] = NODATA
    return _write(path, data, GRID_TRANSFORM, "EPSG:4326", NODATA)


def fine_raster(path: Path, value: float) -> Path:
    """Same extent at half the pixel size (128x128, 0.0005 deg) — different resolution."""
    data = np.full((128, 128), value, dtype=np.float32)
    data[:8, :] = NODATA
    data[:, :8] = NODATA
    return _write(path, data, Affine(0.0005, 0.0, 78.0, 0.0, -0.0005, 18.0), "EPSG:4326", NODATA)


def utm_raster(path: Path) -> Path:
    """Projected (EPSG:32644, 100 m) int32 raster with value = row * 1000 + col; NoData = -1 on row 0."""
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
    x0, y0 = to_utm.transform(77.995, 18.005)
    x0, y0 = float(np.floor(x0 / 100) * 100), float(np.ceil(y0 / 100) * 100)
    rows, cols = np.mgrid[0:90, 0:90]
    data = (rows * 1000 + cols).astype(np.int32)
    data[0, :] = -1
    return _write(path, data, Affine(100.0, 0.0, x0, 0.0, -100.0, y0), "EPSG:32644", -1)


def unmasked_int_raster(path: Path) -> Path:
    """int16 count raster with NO NoData value at all (exercises the mask-band export path)."""
    data = np.full((GRID_SIZE, GRID_SIZE), 3, dtype=np.int16)
    return _write(path, data, GRID_TRANSFORM, "EPSG:4326", None)


def multiband_raster(path: Path) -> Path:
    data = np.ones((2, 8, 8), dtype=np.float32)
    with rasterio.open(
        path, "w", driver="GTiff", dtype="float32", count=2, width=8, height=8,
        crs="EPSG:4326", transform=GRID_TRANSFORM, nodata=NODATA,
    ) as dst:
        dst.write(data)
    return path


def brute_force_sum(path: Path, aoi_lonlat, nodata) -> tuple[int, float]:
    """Independent reference: test every pixel center (transformed to lon/lat) against the AOI."""
    from shapely import contains_xy
    from shapely.geometry import shape

    geom = shape(aoi_lonlat)
    with rasterio.open(path) as src:
        arr = src.read(1)
        rows, cols = np.mgrid[0 : src.height, 0 : src.width]
        xs, ys = rasterio.transform.xy(src.transform, rows.ravel(), cols.ravel(), offset="center")
        to_ll = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
        lon, lat = to_ll.transform(np.asarray(xs), np.asarray(ys))
    inside = contains_xy(geom, lon, lat).reshape(arr.shape)
    valid = inside & (arr != nodata)
    return int(valid.sum()), float(arr[valid].astype(np.float64).sum())
