# Test helpers: synthetic GeoTIFFs on the same grid as the ingestion fixtures,
# standard AOIs, request payloads and an independent brute-force zonal reference. Values are SYNTHETIC.

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

ZONAL = "/operations/zonal-statistics"
CLIP = "/operations/clip"


# GeoJSON rectangle polygon. Used to build the AOIs below and in tests.
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


# Request body for clip/zonal-statistics with sensible defaults. Used by most tests.
def payload(dataset_id="synthetic-worldpop-2001", aoi=TOWN_A, aoi_crs="EPSG:4326", **extra) -> dict:
    return {"dataset_id": dataset_id, "aoi": aoi, "aoi_crs": aoi_crs, **extra}


# Write a single-band DEFLATE GeoTIFF tagged SYNTHETIC. Used by the raster builders below.
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


# Contract grid filled with `value` (1 for 2001, 2 for 2011) and the NoData strips. Used by conftest.
def contract_raster(path: Path, value: float) -> Path:
    data = np.full((GRID_SIZE, GRID_SIZE), value, dtype=np.float32)
    data[:NODATA_STRIP, :] = NODATA
    data[:, :NODATA_STRIP] = NODATA
    return _write(path, data, GRID_TRANSFORM, "EPSG:4326", NODATA)


# Same extent at half the pixel size (128x128, 0.0005 deg). Used by conftest.
def fine_raster(path: Path, value: float) -> Path:
    data = np.full((128, 128), value, dtype=np.float32)
    data[:8, :] = NODATA
    data[:, :8] = NODATA
    return _write(path, data, Affine(0.0005, 0.0, 78.0, 0.0, -0.0005, 18.0), "EPSG:4326", NODATA)


# Projected (EPSG:32644, 100 m) int32 raster, value = row * 1000 + col, NoData -1 on row 0. Used by conftest.
def utm_raster(path: Path) -> Path:
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
    x0, y0 = to_utm.transform(77.995, 18.005)
    x0, y0 = float(np.floor(x0 / 100) * 100), float(np.ceil(y0 / 100) * 100)
    rows, cols = np.mgrid[0:90, 0:90]
    data = (rows * 1000 + cols).astype(np.int32)
    data[0, :] = -1
    return _write(path, data, Affine(100.0, 0.0, x0, 0.0, -100.0, y0), "EPSG:32644", -1)


# int16 count raster with no NoData value (exercises the mask-band export path). Used by conftest.
def unmasked_int_raster(path: Path) -> Path:
    data = np.full((GRID_SIZE, GRID_SIZE), 3, dtype=np.int16)
    return _write(path, data, GRID_TRANSFORM, "EPSG:4326", None)


# Two-band raster (must be rejected). Used by conftest.
def multiband_raster(path: Path) -> Path:
    data = np.ones((2, 8, 8), dtype=np.float32)
    with rasterio.open(
        path, "w", driver="GTiff", dtype="float32", count=2, width=8, height=8,
        crs="EPSG:4326", transform=GRID_TRANSFORM, nodata=NODATA,
    ) as dst:
        dst.write(data)
    return path


# Independent reference: test every pixel center (in lon/lat) against the AOI; returns (count, sum).
# Used by test_zonal and test_clip.
def brute_force_sum(path: Path, aoi_lonlat, nodata) -> tuple[int, float]:
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
