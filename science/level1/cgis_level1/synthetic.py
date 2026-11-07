"""Deterministic SYNTHETIC fixtures that mimic the real input geometry.

* WorldPop-like grid: EPSG:4326, 3 arc-second cells, origin on the integer-degree lattice, a size that is
  *not* a multiple of the parent block (partial edge parents), a NoData "sea" and NoData holes.
* GHSL-like grids: same cell size but shifted by half a cell (as GHSL R2023A 4326_3ss is relative to
  WorldPop), split into two tiles (VRT mosaic), uint16 m2, with NRES > total cells and zero areas.
* GHS-POP-like counts for epochs 2000/2005/2010/2015.
Values are generated from a fixed seed; nothing here is a real population estimate.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS

RES = 1.0 / 1200.0
X0, Y0 = 77.0, 13.0
H, W = 230, 245          # not multiples of 10 -> partial edge parents
WP_NODATA = -99999.0
YEARS = list(range(2001, 2012))
EPOCHS = [2000, 2005, 2010, 2015]


def _write(path: Path, arr: np.ndarray, transform: Affine, nodata, dtype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype=dtype, crs=CRS.from_epsg(4326), transform=transform, nodata=nodata,
                       tiled=True, blockxsize=64, blockysize=64, compress="deflate") as ds:
        ds.write(arr.astype(dtype), 1)


def make_synthetic(out: Path, seed: int = 20261107) -> dict:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    # "land" mask: sea in the south-east corner, a lake, scattered NoData holes
    land = (xx + yy) < (W + H) * 0.8
    land &= ~(((yy - 60) ** 2 + (xx - 180) ** 2) < 15 ** 2)
    land &= rng.random((H, W)) > 0.002
    # two towns + a village + rural background
    town = 2000 * np.exp(-(((yy - 80) ** 2 + (xx - 70) ** 2) / 300.0)) + \
        800 * np.exp(-(((yy - 160) ** 2 + (xx - 150) ** 2) / 150.0)) + \
        150 * np.exp(-(((yy - 40) ** 2 + (xx - 200) ** 2) / 40.0))
    base_pop = (town * 0.05 + rng.gamma(1.2, 2.0, (H, W))) * land
    wp_t = Affine(RES, 0, X0, 0, -RES, Y0)
    info = {"worldpop": {}, "ghsl": {}, "pop": {}}
    for i, y in enumerate(YEARS):
        growth = 1.0 + 0.02 * i
        pop = np.where(land, base_pop * growth * (1 + 0.01 * np.sin(i + xx / 30.0)), WP_NODATA)
        p = out / "worldpop" / f"ind_ppp_{y}.tif"
        _write(p, pop, wp_t, WP_NODATA, "float32")
        info["worldpop"][y] = str(p)

    # GHSL lattice shifted by half a cell; covers the WorldPop extent + margin; two tiles split at column gw//2
    gh, gw = H + 6, W + 6
    gx0, gy0 = X0 - 3.5 * RES, Y0 + 3.5 * RES
    gyy, gxx = np.mgrid[0:gh, 0:gw]
    gland = np.pad(land, 3, constant_values=False)
    gland = np.pad(land, ((3, 3), (3, 3)), constant_values=False)
    cell_m2 = 8500.0
    gtown = 2000 * np.exp(-(((gyy - 83) ** 2 + (gxx - 73) ** 2) / 300.0)) + \
        800 * np.exp(-(((gyy - 163) ** 2 + (gxx - 153) ** 2) / 150.0)) + \
        150 * np.exp(-(((gyy - 43) ** 2 + (gxx - 203) ** 2) / 40.0))
    for j, e in enumerate(EPOCHS):
        frac = np.clip(gtown / 2000.0 * (0.6 + 0.1 * j), 0, 0.95)
        total = np.round(cell_m2 * frac * gland).astype(np.uint16)
        nres = np.round(total * 0.25).astype(np.int64)
        # NRES > total in a few cells (source inconsistency), and an industrial block with total == NRES
        nres[100:103, 100:110] = total[100:103, 100:110] + 50
        nres[20:30, 20:30] = total[20:30, 20:30]
        nres = nres.astype(np.uint16)
        gpop = np.round(gtown * 0.04 * (1 + 0.1 * j) + gland * 1.5, 3)
        for kind, arr, dtype in (("total", total, "uint16"), ("nres", nres, "uint16"), ("pop", gpop, "float64")):
            d = out / "ghsl" / f"{kind}_E{e}"
            half = gw // 2
            for k, (cs, ce) in enumerate(((0, half), (half, gw))):
                t = Affine(RES, 0, gx0 + cs * RES, 0, -RES, gy0)
                _write(d / f"{kind}_E{e}_C{k}.tif", arr[:, cs:ce], t, None, dtype)
            info["ghsl" if kind != "pop" else "pop"][f"{kind}_{e}"] = str(d)
    # synthetic zones: two "districts" split by a diagonal, as GeoJSON
    xm = X0 + W * RES / 2
    zones = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"zid": "syn-d1", "name": "SYNTHETIC West"},
         "geometry": {"type": "Polygon", "coordinates": [[[X0 - 1e-3, Y0 + 1e-3], [xm, Y0 + 1e-3],
                                                           [xm + 0.02, Y0 - H * RES - 1e-3],
                                                           [X0 - 1e-3, Y0 - H * RES - 1e-3], [X0 - 1e-3, Y0 + 1e-3]]]}},
        {"type": "Feature", "properties": {"zid": "syn-d2", "name": "SYNTHETIC East"},
         "geometry": {"type": "Polygon", "coordinates": [[[xm, Y0 + 1e-3], [X0 + W * RES + 1e-3, Y0 + 1e-3],
                                                           [X0 + W * RES + 1e-3, Y0 - H * RES - 1e-3],
                                                           [xm + 0.02, Y0 - H * RES - 1e-3], [xm, Y0 + 1e-3]]]}}]}
    zp = out / "zones" / "synthetic_districts.geojson"
    zp.parent.mkdir(parents=True, exist_ok=True)
    zp.write_text(json.dumps(zones))
    info["zones"] = str(zp)
    (out / "SYNTHETIC_README.txt").write_text("SYNTHETIC fixtures generated by cgis_level1.synthetic; "
                                              f"seed={seed}. Not real population data.\n")
    return info
