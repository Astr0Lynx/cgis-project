"""Write a SYNTHETIC raster on the exact national Level 1 grid, block by block (bounded memory).

Grid (Agent 2 handoff, all eleven india-l1-pop-YYYY): EPSG:4326, 35075 x 34497 cells,
origin (68.185416408, 35.501250059), pixel 0.00083333333 deg (3 arc-seconds), float32, NoData -99999.
Values are a deterministic smooth field (a few "city" peaks over a low rural background) and cells
outside a coarse India-shaped polygon are NoData. The file exists to measure platform behaviour at
national size (ingestion RSS/time/disk, tiles, zonal/clip jobs); it is NOT population data.

    python make_national_raster.py OUT.tif [--scale 1.0]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np
import rasterio
from affine import Affine
from rasterio.features import geometry_mask
from rasterio.windows import Window

WIDTH, HEIGHT = 35075, 34497
RES = 0.0008333333300071276
TRANSFORM = Affine(RES, 0.0, 68.185416408, 0.0, -0.0008333333299997101, 35.501250059)
NODATA = -99999.0
# Coarse outline of India (lon, lat), deliberately simplified; SYNTHETIC mask only.
OUTLINE = [(68.2, 23.6), (70.0, 20.7), (72.8, 19.0), (73.4, 15.8), (74.8, 12.8), (76.3, 9.6),
           (77.5, 8.1), (78.2, 9.0), (80.2, 13.0), (80.3, 15.8), (82.4, 17.0), (85.1, 19.4),
           (86.9, 21.4), (89.0, 21.6), (92.6, 21.9), (93.8, 24.0), (95.2, 26.5), (97.3, 28.2),
           (94.6, 29.3), (91.6, 27.8), (88.1, 27.9), (84.1, 28.6), (80.9, 30.2), (79.0, 32.4),
           (78.7, 35.4), (75.9, 35.4), (73.8, 34.4), (74.6, 32.3), (73.9, 30.3), (71.2, 27.8),
           (70.1, 26.4), (68.2, 23.6)]
CITIES = [(72.88, 19.08, 60.0), (77.21, 28.61, 70.0), (88.36, 22.57, 55.0), (80.27, 13.08, 45.0),
          (77.59, 12.97, 45.0), (78.47, 17.39, 40.0), (72.57, 23.02, 30.0), (73.86, 18.52, 30.0)]


def block_values(win: Window) -> np.ndarray:
    rows = np.arange(int(win.row_off), int(win.row_off + win.height), dtype=np.float64)
    cols = np.arange(int(win.col_off), int(win.col_off + win.width), dtype=np.float64)
    lon = TRANSFORM.c + (cols + 0.5) * TRANSFORM.a
    lat = TRANSFORM.f + (rows + 0.5) * TRANSFORM.e
    lon2, lat2 = np.meshgrid(lon, lat)
    v = 1.5 + 0.5 * np.sin(lon2 * 3.1) * np.cos(lat2 * 2.7)
    for cx, cy, peak in CITIES:
        v += peak * np.exp(-((lon2 - cx) ** 2 + (lat2 - cy) ** 2) / 0.02)
    return np.round(v, 2).astype(np.float32)   # 2 decimals: realistic DEFLATE ratio, deterministic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--block", type=int, default=1024)
    args = ap.parse_args(argv)
    outline = {"type": "Polygon", "coordinates": [OUTLINE]}
    profile = {
        "driver": "GTiff", "width": WIDTH, "height": HEIGHT, "count": 1, "dtype": "float32",
        "crs": "EPSG:4326", "transform": TRANSFORM, "nodata": NODATA, "tiled": True,
        "blockxsize": 512, "blockysize": 512, "compress": "deflate", "predictor": 3, "BIGTIFF": "YES",
    }
    t0 = time.monotonic()
    valid = 0
    total = 0.0
    with rasterio.Env(GDAL_CACHEMAX=256), rasterio.open(args.out, "w", **profile) as dst:
        dst.update_tags(SYNTHETIC="TRUE", NOTICE="SYNTHETIC national-grid benchmark; not population data")
        dst.set_band_unit(1, "people_per_cell")
        b = args.block
        for r in range(0, HEIGHT, b):
            for c in range(0, WIDTH, b):
                win = Window(c, r, min(b, WIDTH - c), min(b, HEIGHT - r))
                inside = geometry_mask([outline], out_shape=(int(win.height), int(win.width)),
                                       transform=dst.window_transform(win), invert=True)
                data = block_values(win)
                data[~inside] = NODATA
                valid += int(inside.sum())
                total += float(data[inside].astype(np.float64).sum())
                dst.write(data, 1, window=win)
    print(json.dumps({"file": args.out, "width": WIDTH, "height": HEIGHT, "cells": WIDTH * HEIGHT,
                      "valid_cells": valid, "sum": total, "seconds": round(time.monotonic() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
