"""Unit tests that need pygeoapi itself; run inside the ogc image:

    docker run --rm -v "$PWD/tests:/cgis/tests:ro" --entrypoint /venv/bin/python cgis-ogc:dev \
        -m pytest -q -p no:cacheprovider /cgis/tests/unit_pygeoapi
"""

import struct
import zlib

import pytest

from cgis_ogc import conformance
from cgis_ogc.pygeoapi_plugins.postgresql import is_datetime_param
from cgis_ogc.pygeoapi_plugins.titiler_tiles import TiTilerTileProvider, blank_png, parse_rescale

TILE_PROVIDER = conformance.TILE_PROVIDER
FEATURE_PROVIDER = conformance.FEATURE_PROVIDER
BBOX = [78.0, 17.936, 78.064, 18.0]


def tile_def(**overrides):
    provider = {
        "type": "tile", "name": TILE_PROVIDER, "data": "synthetic-worldpop-2001",
        "format": {"name": "png", "mimetype": "image/png"},
        "options": {"schemes": ["WebMercatorQuad", "WorldCRS84Quad"], "zoom": {"min": 0, "max": 13}, "bbox": BBOX},
    }
    provider.update(overrides)
    return provider


def config(*providers):
    return {"resources": {"c": {"type": "collection", "providers": list(providers)}}}


# -- configuration invariants -------------------------------------------------

def test_valid_config_accepted():
    conformance.validate_config(config(tile_def(), {"type": "feature", "name": FEATURE_PROVIDER}))


@pytest.mark.parametrize("cfg", [
    config({"type": "feature", "name": FEATURE_PROVIDER, "editable": True}),
    config({"type": "feature", "name": "PostgreSQL"}),
    config(tile_def(name="MVT-tippecanoe")),
    config(tile_def(format={"name": "mvt", "mimetype": "application/vnd.mapbox-vector-tile"})),
    config({"type": "coverage", "name": "rasterio"}),
    {"resources": {"p": {"type": "process", "processor": {"name": "HelloWorld"}}}},
])
def test_configs_that_would_falsify_conformance_are_refused(cfg):
    with pytest.raises(conformance.UnsupportedConfiguration):
        conformance.validate_config(cfg)


def test_conformance_lists_corrected():
    conformance.apply(config(tile_def()))
    from pygeoapi.api import itemtypes, tiles

    declared = set(tiles.CONFORMANCE_CLASSES) | set(itemtypes.CONFORMANCE_CLASSES_FEATURES)
    assert conformance.TILES_PNG in declared
    assert not declared & conformance.WITHDRAWN


@pytest.mark.parametrize("accept,expected", [
    (None, True), ("", True), ("image/png", True), ("image/*", True), ("*/*;q=0.1", True),
    ("text/html,application/xhtml+xml,*/*;q=0.8", True), ("image/jpeg", False),
    ("image/png;q=0", False), ("application/json", False),
])
def test_accepts_png(accept, expected):
    assert conformance.accepts_png(accept) is expected


@pytest.mark.parametrize("value,ok", [
    ("2001-01-01", True), ("2001-01-01T00:00:00Z", True), ("2001-01-01T00:00:00+05:30", True),
    ("2001-01-01/..", True), ("../2001-01-01T00:00:00Z", True), ("/2001-01-01", True), ("../..", True),
    ("notadate", False), ("2001-13-01", False), ("a/b", False), ("2001/2002/2003", False),
])
def test_datetime_syntax(value, ok):
    assert is_datetime_param(value) is ok


# -- tile provider ------------------------------------------------------------

def test_provider_rejects_paths_and_urls_as_data():
    for data in ("/data/assets/x.tif", "http://evil/x.tif", "../x", ""):
        with pytest.raises(Exception):
            TiTilerTileProvider(tile_def(data=data))


def test_provider_rejects_non_png():
    with pytest.raises(RuntimeError):
        TiTilerTileProvider(tile_def(format={"name": "jpeg", "mimetype": "image/jpeg"}))


def test_tileset_metadata_members():
    options = dict(tile_def()["options"], rescale=[0, 2])
    p = TiTilerTileProvider(tile_def(options=options))
    md = p.get_metadata(dataset="c", server_url="http://gw/ogc", tileset="WebMercatorQuad", metadata_format="json")
    assert md["dataType"] == "map"
    assert md["style"]["title"] == "viridis linear 0..2" and "shared range" in md["style"]["description"]
    assert md["crs"] == "http://www.opengis.net/def/crs/EPSG/0/3857"
    assert md["tileMatrixSetURI"] == "http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad"
    rels = {link["rel"]: link for link in md["links"]}
    assert rels["http://www.opengis.net/def/rel/ogc/1.0/tiling-scheme"]["href"].startswith("http://gw/ogc/TileMatrixSets/")
    item = rels["item"]
    assert item["templated"] is True and item["type"] == "image/png"
    assert all(v in item["href"] for v in ("{tileMatrix}", "{tileRow}", "{tileCol}"))
    assert [limit["tileMatrix"] for limit in md["tileMatrixSetLimits"]] == [str(z) for z in range(14)]


def test_tilejson_only_for_webmercator():
    p = TiTilerTileProvider(tile_def())
    tj = p.get_metadata(dataset="c", server_url="http://gw/ogc", tileset="WebMercatorQuad", metadata_format="tilejson")
    assert tj["tilejson"] == "3.0.0" and tj["bounds"] == BBOX
    assert tj["tiles"][0].endswith("/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png")
    with pytest.raises(Exception):
        p.get_metadata(dataset="c", server_url="http://gw/ogc", tileset="WorldCRS84Quad", metadata_format="tilejson")


@pytest.mark.parametrize("tileset,z,y,x", [
    ("NoSuchTMS", 11, 919, 1467), ("WebMercatorQuad", 14, 0, 0), ("WebMercatorQuad", -1, 0, 0),
    ("WebMercatorQuad", 11, 0, 0), ("WebMercatorQuad", 11, 2048, 1467), ("WebMercatorQuad", "a", 1, 1),
])
def test_out_of_range_tiles_raise_before_any_network_call(tileset, z, y, x, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network call"))
    with pytest.raises(Exception) as err:
        TiTilerTileProvider(tile_def()).get_tiles(tileset=tileset, z=z, y=y, x=x)
    assert getattr(err.value, "http_status_code", 404) == 404


@pytest.mark.parametrize("value,expected", [
    ("0,2", [0.0, 2.0]), ([1, 5], [1.0, 5.0]), (None, None), ("", None), ("2,1", None), ("a,b", None), ("1", None),
])
def test_parse_rescale(value, expected):
    assert parse_rescale(value) == expected


def test_env_rescale_applies_when_no_option(monkeypatch):
    monkeypatch.setenv("OGC_TILE_RESCALE", "0,2")
    assert TiTilerTileProvider(tile_def()).rescale == [0.0, 2.0]


def test_blank_png_is_valid_transparent_rgba():
    data = blank_png(4, 2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert (width, height, depth, colour) == (4, 2, 8, 6)
    idat_len = struct.unpack(">I", data[33:37])[0]
    raw = zlib.decompress(data[41:41 + idat_len])
    assert raw == (b"\x00" + b"\x00" * 16) * 2
