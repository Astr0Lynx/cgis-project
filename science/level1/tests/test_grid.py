# Grid alignment, GHSL tile mosaicking and reprojected-grid (general warp) weight transfer.

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS

from cgis_level1.grid import (TargetGrid, WeightSource, build_mosaic_vrt, grid_from_raster,
                              source_mapping)

RES = 1 / 1200


# Writes a small test GeoTIFF.
def _tif(path, arr, transform, crs=4326, nodata=None, dtype=None):
    dtype = dtype or arr.dtype.name
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype=dtype,
                       crs=CRS.from_epsg(crs), transform=transform, nodata=nodata) as ds:
        ds.write(arr.astype(dtype), 1)
    return path


# TargetGrid over a tiny test grid.
def _target(t, h, w):
    return TargetGrid(CRS.from_epsg(4326).to_wkt(), 4326, t, w, h, 0, 0, h, w)


# Checks: half pixel mapping like ghsl.
def test_half_pixel_mapping_like_ghsl():
    tgt = _target(Affine(RES, 0, 70.0, 0, -RES, 30.0), 100, 100)
    # GHSL tile edge 69.99208307850 (9.5 px west of 70) and 29.0995834 style offset
    src_t = Affine(RES, 0, 70.0 - 9.5 * RES, 0, -RES, 30.0 + 0.5 * RES)
    m = source_mapping(tgt, src_t, (12000, 12000))
    assert m.same_res and m.iu == 9 and m.fx == pytest.approx(0.5) and m.iv == 0 and m.fy == pytest.approx(0.5)


# Checks: snapping tiny offsets.
def test_snapping_tiny_offsets():
    tgt = _target(Affine(RES, 0, 70.0, 0, -RES, 30.0), 10, 10)
    m = source_mapping(tgt, Affine(RES, 0, 70.0 - 5 * RES + 1e-10, 0, -RES, 30.0), (100, 100))
    assert m.fx == 0.0 and m.iu == 5


# Checks: mosaic and seam free read.
def test_mosaic_and_seam_free_read(tmp_path):
    # two GHSL-like tiles side by side, half-pixel offset; reading across the tile seam must equal
    # reading the same values from one unsplit raster
    rng = np.random.default_rng(0)
    full = rng.integers(0, 8000, (40, 60)).astype(np.uint16)
    x0, y0 = 70.0 - 2.5 * RES, 30.0 + 2.5 * RES
    (tmp_path / "a").mkdir()
    _tif(tmp_path / "a" / "t1.tif", full[:, :30], Affine(RES, 0, x0, 0, -RES, y0))
    _tif(tmp_path / "a" / "t2.tif", full[:, 30:], Affine(RES, 0, x0 + 30 * RES, 0, -RES, y0))
    _tif(tmp_path / "single.tif", full, Affine(RES, 0, x0, 0, -RES, y0))
    vrt = build_mosaic_vrt(str(tmp_path / "a" / "*.tif"), tmp_path / "m.vrt")
    tgt = _target(Affine(RES, 0, 70.0, 0, -RES, 30.0), 30, 50)
    a, _ = WeightSource(vrt, tgt).read(0, 0, 30, 50)
    b, _ = WeightSource(tmp_path / "single.tif", tgt).read(0, 0, 30, 50)
    assert np.array_equal(a, b)
    # tile-independent: reading two halves equals reading the whole
    ws = WeightSource(vrt, tgt)
    left, _ = ws.read(0, 0, 30, 20)
    right, _ = ws.read(0, 20, 30, 30)
    assert np.array_equal(np.hstack([left, right]), a)


# Checks: outside footprint counted.
def test_outside_footprint_counted(tmp_path):
    _tif(tmp_path / "s.tif", np.ones((10, 10), np.uint16), Affine(RES, 0, 70.0, 0, -RES, 30.0))
    tgt = _target(Affine(RES, 0, 70.0, 0, -RES, 30.0), 20, 10)
    v, n_out = WeightSource(tmp_path / "s.tif", tgt).read(0, 0, 20, 10)
    assert n_out == 100 and v[:10].sum() == 100 and v[10:].sum() == 0


# Checks: source nodata becomes missing.
def test_source_nodata_becomes_missing(tmp_path):
    arr = np.full((5, 5), 7, np.uint16)
    arr[2, 2] = 65535
    _tif(tmp_path / "s.tif", arr, Affine(RES, 0, 70.0, 0, -RES, 30.0), nodata=65535)
    tgt = _target(Affine(RES, 0, 70.0, 0, -RES, 30.0), 4, 4)
    v, _ = WeightSource(tmp_path / "s.tif", tgt).read(0, 0, 4, 4)
    assert np.isnan(v[2, 2]) and v[0, 0] == 7


# Checks: reprojected grid general path conserves sum.
def test_reprojected_grid_general_path_conserves_sum(tmp_path):
    # source at 2x coarser resolution: general (GDAL 'sum') path must conserve the extensive total
    src = np.zeros((10, 10), np.float32)
    src[2:8, 2:8] = 100.0
    _tif(tmp_path / "coarse.tif", src, Affine(2 * RES, 0, 70.0, 0, -2 * RES, 30.0))
    tgt = _target(Affine(RES, 0, 70.0, 0, -RES, 30.0), 20, 20)
    ws = WeightSource(tmp_path / "coarse.tif", tgt)
    assert not ws.map.same_res
    v, _ = ws.read(0, 0, 20, 20)
    assert v.sum() == pytest.approx(src.sum(), rel=1e-3)
    assert v[6, 6] == pytest.approx(25.0, rel=1e-3)


# Checks: bbox snaps to parent lattice.
def test_bbox_snaps_to_parent_lattice(tmp_path):
    p = _tif(tmp_path / "wp.tif", np.ones((95, 87), np.float32), Affine(RES, 0, 70.0, 0, -RES, 30.0), nodata=-99999)
    g = grid_from_raster(p, 10, (70.0 + 13.2 * RES, 30.0 - 47.5 * RES, 70.0 + 31 * RES, 30.0 - 21 * RES))
    assert g.col0 % 10 == 0 and g.row0 % 10 == 0
    assert (g.col0, g.row0) == (10, 20) and g.col0 + g.width == 40 and g.row0 + g.height == 50
    full = grid_from_raster(p, 10)
    assert (full.width, full.height) == (87, 95)
    tiles = full.tiles(30)
    assert sum(h * w for _, _, h, w in tiles) == 87 * 95       # no omission
    cells = set()
    for r, c, h, w in tiles:
        for rr in range(r, r + h):
            for cc in range(c, c + w):
                assert (rr, cc) not in cells                     # no double counting
                cells.add((rr, cc))


# Checks: replace retry survives transient lock.
def test_replace_retry_survives_transient_lock(tmp_path, monkeypatch):
    import os as _os
    from cgis_level1 import grid as g
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    calls = {"n": 0}
    real = _os.replace

    # os.replace stand-in that fails twice with PermissionError.
    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("WinError 5")
        return real(a, b)
    monkeypatch.setattr(g.os, "replace", flaky)
    g.replace_retry(src, dst)
    assert dst.read_text() == "x" and calls["n"] == 3
