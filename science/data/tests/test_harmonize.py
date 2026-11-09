import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from cgis_data import harmonize as H


def test_block_sum_conserves_counts_and_keeps_nodata(make_raster, tmp_path):
    rng = np.random.default_rng(0)
    d = rng.integers(0, 50, (16, 16)).astype("float32")
    d[0:4, 0:4] = -99999                         # whole 4x4 block NoData
    d[4, 4] = -99999                              # partial block: remaining 15 cells still counted
    src = make_raster(data=d)
    out = str(tmp_path / "o.tif")
    rep = H.block_sum(src, out, 4)
    assert rep["abs_diff"] == 0
    with rasterio.open(out) as ds:
        a = ds.read(1)
        assert a[0, 0] == -99999 and a[1, 1] == d[4:8, 4:8][d[4:8, 4:8] != -99999].sum()
        assert ds.nodata == -99999 and ds.res == (0.04, 0.04)


def test_raster_sum_ignores_nodata(make_raster):
    d = np.ones((8, 8), "float32"); d[0, :] = -99999
    s, nv, nn = H.raster_sum(make_raster(data=d))
    assert (s, nv, nn) == (56.0, 56, 8)


def test_nan_nodata(make_raster):
    d = np.ones((8, 8), "float32"); d[0, 0] = np.nan
    s, nv, nn = H.raster_sum(make_raster(data=d, nodata=float("nan")))
    assert (s, nv, nn) == (63.0, 63, 1)


def test_crop_to_bbox_preserves_values_and_georef(make_raster, tmp_path):
    src = make_raster(data=np.arange(100, dtype="float32").reshape(10, 10))   # lon 77..77.1 lat 17.9..18
    out = str(tmp_path / "c.tif")
    info = H.crop_to_bbox(src, out, (77.02, 17.93, 77.05, 17.98))
    with rasterio.open(src) as s, rasterio.open(out) as d:
        assert d.nodata == s.nodata and d.crs == s.crs and d.dtypes == s.dtypes
        win = H.snap_window_outward(rasterio.windows.from_bounds(*d.bounds, s.transform))
        np.testing.assert_array_equal(d.read(1), s.read(1, window=win))
    assert info["width"] == 3 or info["width"] == 4
    with pytest.raises(ValueError):
        H.crop_to_bbox(src, out, (10, 10, 11, 11))


def test_mollweide_to_4326_area_conserved(make_raster, tmp_path):
    """Non-EPSG source CRS (ESRI:54009) -> EPSG:4326 with area-conserving warp; NoData stays NoData."""
    from pyproj import Transformer
    x0, y0 = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True).transform(77.0, 18.0)
    rng = np.random.default_rng(1)
    d = (rng.random((120, 120)) * 10000).astype("float32")
    d[:10, :10] = -200                                         # NoData block
    src = make_raster("moll.tif", data=d, crs="ESRI:54009", transform=from_origin(x0, y0, 100, 100), nodata=-200.0)
    # target: 4326 grid slightly larger than the source footprint so all mass lands inside
    t = from_origin(76.95, 18.05, 0.0009, 0.0009)
    out = str(tmp_path / "w.tif")
    rep = H.warp_area_quantity(src, out, "EPSG:4326", t, 200, 160, 10000.0,
                               dst_cell_area_fn=H.cell_area_m2_rows, dst_nodata=-1.0)
    assert abs(rep["relative_diff"]) < 0.02, rep          # documented tolerance for edge effects
    with rasterio.open(out) as ds:
        a = ds.read(1)
        assert ds.nodata == -1.0 and (a == -1.0).any() and (a[a != -1.0] >= 0).all()


def test_categorical_nearest_never_creates_new_classes(make_raster, tmp_path):
    from pyproj import Transformer
    classes = np.array([1, 3, 7, 12], dtype="uint8")
    rng = np.random.default_rng(2)
    d = rng.choice(classes, (64, 64)).astype("uint8")
    x0, y0 = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True).transform(77.0, 18.0)
    src = make_raster("lulc.tif", data=d, dtype="uint8", nodata=0, crs="EPSG:32644", transform=from_origin(x0, y0, 30, 30))
    out = str(tmp_path / "l.tif")
    H.warp_categorical(src, out, "EPSG:4326", from_origin(77.0, 18.0, 0.00025, 0.00025), 60, 60, dst_nodata=0)
    with rasterio.open(out) as ds:
        vals = set(np.unique(ds.read(1)).tolist()) - {0}
    assert vals <= set(classes.tolist()) and vals


def test_cell_area_decreases_with_latitude():
    t = from_origin(77.0, 40.0, 0.001, 0.001)
    rows = H.cell_area_m2_rows(t, 3)
    assert rows[0] < H.cell_area_m2_rows(from_origin(77.0, 5.0, 0.001, 0.001), 1)[0]
    assert 8000 < rows[0] < 10000          # ~100m cell at 3 arc-sec, high latitude
