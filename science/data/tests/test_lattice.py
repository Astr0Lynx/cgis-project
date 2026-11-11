# Tests for lattice registration, aligned window copies, GHSL tile index and tile fetching.
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from cgis_data import harmonize as H
from cgis_data import ghsl_tiles as G
from cgis_data import fetch as F
from cgis_data import sources as S

PIX = 0.00083333333


# Whole-pixel offsets are found; off-lattice or different pixel sizes raise.
def test_lattice_offset_whole_pixels_and_rejections():
    ref = from_origin(68.0, 35.0, PIX, PIX)
    assert H.lattice_offset(ref, from_origin(68.0 - PIX, 35.0, PIX, PIX)) == (-1, 0)       # WorldPop-2010-style extra west column
    assert H.lattice_offset(ref, from_origin(68.0 + 5 * PIX, 35.0 - 3 * PIX, PIX, PIX)) == (5, 3)
    with pytest.raises(ValueError, match="one lattice"):
        H.lattice_offset(ref, from_origin(68.0 + 0.5 * PIX, 35.0, PIX, PIX))
    with pytest.raises(ValueError, match="pixel size"):
        H.lattice_offset(ref, from_origin(68.0, 35.0, PIX * 2, PIX * 2))


# Writes a small EPSG:4326 GeoTIFF.
def _write(path, data, tr, nodata):
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1, dtype=data.dtype,
                       crs="EPSG:4326", transform=tr, nodata=nodata) as ds:
        ds.write(data, 1)


# Aligned copy is byte-exact and fills uncovered cells with NoData.
def test_extract_aligned_window_exact_copy_and_nodata_fill(tmp_path):
    d = np.arange(100, dtype="uint16").reshape(10, 10)
    src = tmp_path / "g.tif"
    _write(src, d, from_origin(70.0, 30.0, PIX, PIX), 65535)
    ref = from_origin(70.0 + 4 * PIX, 30.0 - 2 * PIX, PIX, PIX)           # window shifted (4,2), extends past source
    out = tmp_path / "o.tif"
    rep = H.extract_aligned_window(str(src), str(out), ref, 8, 6)
    with rasterio.open(out) as ds:
        a = ds.read(1)
        assert ds.dtypes[0] == "uint16" and ds.nodata == 65535 and ds.transform == ref
    np.testing.assert_array_equal(a[:6, :6], d[2:8, 4:10])                  # byte-exact
    assert (a[:, 6:] == 65535).all()                                          # outside source -> NoData, not 0
    assert rep["offset_cols"] == -4 and rep["offset_rows"] == -2


# An off-lattice source is rejected.
def test_extract_aligned_window_rejects_off_lattice(tmp_path):
    src = tmp_path / "g.tif"
    _write(src, np.zeros((4, 4), "uint16"), from_origin(70.0 + 0.3 * PIX, 30.0, PIX, PIX), 65535)
    with pytest.raises(ValueError):
        H.extract_aligned_window(str(src), str(tmp_path / "o.tif"), from_origin(70.0, 30.0, PIX, PIX), 4, 4)


# 4326 tile index matches real tile origins and WorldPop's lattice.
def test_tile_index_4326_matches_verified_real_tiles_and_wp_lattice():
    assert G.tile_origin_4326(7, 26) == (69.99208307850779, 29.099583419121082)         # read from the real file
    assert G.tiles_for_bbox_4326((77.9, 16.9, 79.1, 17.9)) == [(8, 26)]
    india = G.tiles_for_bbox_4326((68.0, 6.5, 97.5, 37.2))
    assert min(r for r, _ in india) == 6 and max(r for r, _ in india) == 9
    assert min(c for _, c in india) == 25 and max(c for _, c in india) == 28
    # every tile origin sits an integer number of pixels from WorldPop-IND's origin
    wp = from_origin(68.185416408, 35.501250059, G.PIX_4326, G.PIX_4326)
    for r, c in india:
        x, y = G.tile_origin_4326(r, c)
        H.lattice_offset(wp, from_origin(x, y, G.PIX_4326, G.PIX_4326), tol=2e-2)


# A 404 tile is recorded absent and not re-requested.
def test_absent_tile_404_recorded_not_fatal(http_server, tmp_path):
    import dataclasses, json
    served, base = http_server
    (served / "t_R1_C1.zip").write_bytes(b"abc")
    src = dataclasses.replace(next(s for s in S.all_sources() if s.source_id == "india-ghsl-built-total-2010"),
                              tiles={"pattern": base + "/t_R{row}_C{col}.zip", "scheme": "test"})
    st = tmp_path / "s.json"
    res = F.fetch_tiles(src, [(1, 1), (9, 9)], tmp_path, st)
    assert res[1]["status"] == "absent" and res[0]["size"] == 3
    rec = json.loads(st.read_text())["india-ghsl-built-total-2010"]["tiles"]
    assert rec["t_R9_C9.zip"]["status"] == "absent"
    F.fetch_tiles(src, [(9, 9)], tmp_path, st)       # idempotent: absent tile not re-requested


# A source without NoData gets the dtype-max sentinel, never 0.
def test_source_without_nodata_gets_sentinel_not_zero(tmp_path):
    d = np.zeros((4, 4), "uint16"); d[1, 1] = 7                 # zeros are REAL data (no built-up area)
    src = tmp_path / "g.tif"
    with rasterio.open(src, "w", driver="GTiff", height=4, width=4, count=1, dtype="uint16", crs="EPSG:4326",
                       transform=from_origin(70.0, 30.0, PIX, PIX)) as ds:      # no nodata declared
        ds.write(d, 1)
    out = tmp_path / "o.tif"
    H.extract_aligned_window(str(src), str(out), from_origin(70.0 - 2 * PIX, 30.0, PIX, PIX), 8, 4)
    with rasterio.open(out) as ds:
        a = ds.read(1)
        assert ds.nodata == 65535
    assert (a[:, :2] == 65535).all() and (a[:, 2:6] == d).all() and a[1, 3] == 7 and (a[:, 2:6] == 0).sum() == 15
    total, nvalid, nnd = H.raster_sum(str(out))
    assert (total, nvalid, nnd) == (7.0, 16, 16)
