"""Tile-matrix arithmetic checked against morecantile's OGC TMS definitions."""

import morecantile
import pytest

from cgis_ogc.tms import matrix_size, native_max_zoom, tile_matrix_set_limits, tile_range

FIXTURE_BBOX = (78.000, 17.936, 78.064, 18.000)
BBOXES = [FIXTURE_BBOX, (-10.5, 35.2, 3.3, 44.0), (100.0, -45.0, 160.0, -10.0), (-179.0, -60.0, 179.0, 60.0)]


@pytest.mark.parametrize("tms_id", ["WebMercatorQuad", "WorldCRS84Quad"])
@pytest.mark.parametrize("zoom", [0, 1, 5, 11, 13, 18])
def test_matrix_size_matches_registry(tms_id, zoom):
    matrix = morecantile.tms.get(tms_id).matrix(zoom)
    assert matrix_size(tms_id, zoom) == (matrix.matrixWidth, matrix.matrixHeight)


@pytest.mark.parametrize("tms_id", ["WebMercatorQuad", "WorldCRS84Quad"])
@pytest.mark.parametrize("bbox", BBOXES)
@pytest.mark.parametrize("zoom", [0, 3, 8, 11, 13])
def test_tile_range_matches_morecantile_corners(tms_id, bbox, zoom):
    tms = morecantile.tms.get(tms_id)
    west, south, east, north = bbox
    eps = 1e-9  # same inset morecantile.tiles() applies to the bbox edges
    upper_left = tms.tile(west + eps, north - eps, zoom)
    lower_right = tms.tile(east - eps, south + eps, zoom)
    assert tile_range(tms_id, zoom, bbox) == (upper_left.y, lower_right.y, upper_left.x, lower_right.x)


@pytest.mark.parametrize("tms_id", ["WebMercatorQuad", "WorldCRS84Quad"])
@pytest.mark.parametrize("zoom", [0, 8, 11, 13, 16])
def test_fixture_tile_range_matches_enumeration(tms_id, zoom):
    tiles = list(morecantile.tms.get(tms_id).tiles(*FIXTURE_BBOX, zooms=[zoom]))
    rows, cols = [t.y for t in tiles], [t.x for t in tiles]
    assert tile_range(tms_id, zoom, FIXTURE_BBOX) == (min(rows), max(rows), min(cols), max(cols))


def test_limits_cover_requested_zooms():
    limits = tile_matrix_set_limits("WebMercatorQuad", FIXTURE_BBOX, 0, 13)
    assert [entry["tileMatrix"] for entry in limits] == [str(z) for z in range(14)]
    assert limits[0] == {"tileMatrix": "0", "minTileRow": 0, "maxTileRow": 0, "minTileCol": 0, "maxTileCol": 0}


def test_native_max_zoom():
    # 0.001 deg ~ 111 m: WebMercatorQuad z11 (~76 m) is the first finer level, +2 overzoom
    assert native_max_zoom(0.001, geographic=True) == 13
    assert native_max_zoom(100.0, geographic=False) == 13
    assert native_max_zoom(None, geographic=True) == 22
    assert native_max_zoom(1e-9, geographic=True) == 22
