"""OGC API - Tiles publication and the vendor-specific TiTiler routes (TEST_PLAN 5; P0 7)."""

from __future__ import annotations

import io

import morecantile
import pytest
from PIL import Image

from conftest import OGC, RASTER_2001, RASTER_2011, RASTERS, TILES, TOWNS, error_code, ogc_max_zoom

TILES_CORE = "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core"
WMQ = morecantile.tms.get("WebMercatorQuad")
ZOOM = 13      # deepest level advertised for the 64x64 fixture (checked below)


def tile_and_pixel(lon, lat, zoom=ZOOM):
    """WebMercatorQuad tile containing (lon, lat) and the fractional pixel position inside it."""
    t = WMQ.tile(lon, lat, zoom)
    b = WMQ.xy_bounds(t)
    x, y = WMQ.xy(lon, lat)
    return t, (x - b.left) / (b.right - b.left), (b.top - y) / (b.top - b.bottom)


def alpha_at(png: bytes, fx: float, fy: float) -> int:
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    return img.getpixel((min(int(fx * img.width), img.width - 1), min(int(fy * img.height), img.height - 1)))[3]


def ogc_tile(http, dataset, lon, lat):
    t, fx, fy = tile_and_pixel(lon, lat, ogc_max_zoom(http, dataset))
    # OGC API - Tiles path order: {tileMatrix}/{tileRow}/{tileCol}
    resp = http.get(f"{OGC}/collections/{dataset}/tiles/WebMercatorQuad/{t.z}/{t.y}/{t.x}", params={"f": "png"})
    return resp, fx, fy


def test_tiles_conformance_declared(http):
    conf = http.get(f"{OGC}/conformance", params={"f": "json"}).json()["conformsTo"]
    assert TILES_CORE in conf
    assert any("ogcapi-tiles-1/1.0/conf/tileset" in c for c in conf)


@pytest.mark.parametrize("dataset", [RASTER_2001, RASTER_2011])
def test_tileset_discovery_and_metadata(http, dataset):
    tilesets = http.get(f"{OGC}/collections/{dataset}/tiles", params={"f": "json"})
    assert tilesets.status_code == 200
    sets = tilesets.json()["tilesets"]
    wmq = [s for s in sets if s.get("tileMatrixSetURI", "").endswith("WebMercatorQuad")
           or any(link.get("href", "").endswith("/WebMercatorQuad") for link in s.get("links", []))]
    assert wmq, sets
    meta = http.get(f"{OGC}/collections/{dataset}/tiles/WebMercatorQuad", params={"f": "json"})
    assert meta.status_code == 200
    m = meta.json()
    assert m["dataType"] in ("map", "coverage")
    assert m.get("tileMatrixSetURI", "").endswith("WebMercatorQuad") or any(
        "tileMatrixSet" in link.get("rel", "") for link in m["links"])
    assert any(link["rel"] == "item" and "{tileMatrix}" in link["href"] for link in m["links"])
    limits = m["tileMatrixSetLimits"]
    assert limits and ogc_max_zoom(http, dataset) == ZOOM
    # The limits must cover the raster footprint at the deepest level.
    t = WMQ.tile(78.015, 17.985, ZOOM)
    deepest = next(lim for lim in limits if int(lim["tileMatrix"]) == ZOOM)
    assert deepest["minTileRow"] <= t.y <= deepest["maxTileRow"] and deepest["minTileCol"] <= t.x <= deepest["maxTileCol"]


def test_vector_collection_has_no_raster_tileset(http):
    # pygeoapi 0.24 answers 400 (InvalidParameterValue) rather than 404 for a collection without tiles.
    assert http.get(f"{OGC}/collections/{TOWNS}/tiles", params={"f": "json"}).status_code in (400, 404)


def test_tile_matrix_set_definitions(http):
    lst = http.get(f"{OGC}/TileMatrixSets", params={"f": "json"}).json()
    ids = {t["id"] for t in lst["tileMatrixSets"]}
    assert {"WebMercatorQuad", "WorldCRS84Quad"} <= ids
    wmq = http.get(f"{OGC}/TileMatrixSets/WebMercatorQuad", params={"f": "json"}).json()
    assert wmq["id"] == "WebMercatorQuad"
    assert "3857" in str(wmq["crs"])
    assert len(wmq["tileMatrices"]) >= 15
    tm14 = next(tm for tm in wmq["tileMatrices"] if tm["id"] == str(ZOOM))
    assert tm14["matrixWidth"] == tm14["matrixHeight"] == 2 ** ZOOM


@pytest.mark.parametrize("dataset", [RASTER_2001, RASTER_2011])
def test_ogc_tile_renders_valid_pixels_opaque(http, dataset):
    resp, fx, fy = ogc_tile(http, dataset, 78.015, 17.985)        # Town A centre: valid data
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert alpha_at(resp.content, fx, fy) == 255


def test_ogc_tile_nodata_and_outside_are_transparent(http):
    resp, fx, fy = ogc_tile(http, RASTER_2001, 78.0015, 17.9985)  # first rows/cols: NoData strip
    assert resp.status_code == 200
    assert alpha_at(resp.content, fx, fy) == 0
    t, fx, fy = tile_and_pixel(77.995, 17.990)                    # just west of the raster (other tile)
    resp = http.get(f"{OGC}/collections/{RASTER_2001}/tiles/WebMercatorQuad/{t.z}/{t.y}/{t.x}", params={"f": "png"})
    if resp.status_code == 200:
        assert alpha_at(resp.content, fx, fy) == 0
    else:
        assert resp.status_code in (204, 404)


def test_ogc_tile_errors(http):
    base = f"{OGC}/collections/{RASTER_2001}/tiles"
    t = WMQ.tile(78.015, 17.985, ZOOM + 1)                        # below the advertised limits
    assert http.get(f"{base}/WebMercatorQuad/{t.z}/{t.y}/{t.x}", params={"f": "png"}).status_code == 404
    assert http.get(f"{base}/WebMercatorQuad/99/0/0", params={"f": "png"}).status_code in (400, 404)
    assert http.get(f"{base}/NoSuchTMS/14/0/0", params={"f": "png"}).status_code in (400, 404)
    assert http.get(f"{OGC}/collections/no-such-raster/tiles", params={"f": "json"}).status_code == 404


# --- vendor-specific TiTiler routes (/tiles/), NOT an OGC API ---------------------------------------

def test_vendor_dataset_listing_and_tilejson(http):
    listing = http.get(f"{TILES}/datasets")
    assert listing.status_code == 200
    assert {RASTER_2001, RASTER_2011} <= set(map(str, listing.text.replace('"', " ").split()))
    tj = http.get(f"{TILES}/datasets/{RASTER_2001}/WebMercatorQuad/tilejson.json").json()
    assert tj["tiles"][0].startswith("http") and "/tiles/datasets/" in tj["tiles"][0]
    assert tj["bounds"] == pytest.approx([78.0, 17.936, 78.064, 18.0], abs=1e-6)


@pytest.mark.parametrize("year,value", [(2001, 1.0), (2011, 2.0)])
def test_vendor_point_values(http, year, value):
    resp = http.get(f"{TILES}/datasets/{RASTERS[year]}/point/78.015,17.985")
    assert resp.status_code == 200 and resp.json()["values"] == [value]


def test_vendor_point_in_nodata_strip_is_not_a_value(http):
    resp = http.get(f"{TILES}/datasets/{RASTER_2001}/point/78.0015,17.9985")
    if resp.status_code == 200:
        assert resp.json()["values"] in ([None], [-9999.0])
    else:
        assert resp.status_code in (400, 404, 422)


def test_vendor_routes_reject_unknown_and_unsafe_assets(http):
    r = http.get(f"{TILES}/datasets/no-such-dataset/info")
    assert r.status_code == 404 and error_code(r)
    for path in (f"{TILES}/cog/info?url=file:///etc/passwd",
                 f"{TILES}/cog/info?url=http://example.com/x.tif",
                 f"{TILES}/datasets/info?url=/data/assets/rasters",
                 f"{TILES}/datasets/%2e%2e%2fetc%2fpasswd/info",
                 f"{TILES}/datasets/..%2F..%2Fetc%2Fpasswd/info"):
        resp = http.get(path)
        assert resp.status_code in (400, 404, 422), (path, resp.status_code)
        assert "root:" not in resp.text


def test_vector_dataset_is_not_tileable_by_vendor_routes(http):
    assert http.get(f"{TILES}/datasets/{TOWNS}/info").status_code in (404, 422)
