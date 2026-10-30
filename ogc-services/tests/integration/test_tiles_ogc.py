"""OGC API - Tiles 1.0 publication (map tilesets, PNG) over HTTP.

Requirement references are to OGC 20-057 (OGC API - Tiles - Part 1: Core).
"""

import pytest
from conftest import OGC, RASTER_BBOX, RASTERS, REL, get_json, link, png_image, tile_pixel

TMS = {
    "WebMercatorQuad": ("http://www.opengis.net/def/crs/EPSG/0/3857",
                        "http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad"),
    "WorldCRS84Quad": ("http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                       "http://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad"),
}
VALID = (78.030, 17.970)        # valid synthetic cell
NODATA_COL = (78.002, 17.970)   # first 4 columns are NoData
NODATA_ROW = (78.030, 17.998)   # first 4 rows are NoData
ZOOM = 13


def tiles_url(collection):
    return f"{OGC}/collections/{collection}/tiles"


@pytest.mark.parametrize("collection", RASTERS)
def test_collection_links_to_map_tilesets(http, collection):
    doc = get_json(http, f"{OGC}/collections/{collection}?f=json")
    assert link(doc, f"{REL}tilesets-map", "application/json")["href"].startswith(tiles_url(collection))
    assert doc["extent"]["spatial"]["bbox"] == [RASTER_BBOX]
    vendor = link(doc, "alternate", "application/json")
    assert "vendor-specific" in vendor["title"] and "/tiles/datasets/" in vendor["href"]


@pytest.mark.parametrize("collection", RASTERS)
def test_tilesets_list(http, collection):
    """/req/tilesets-list/tileset-links (Req 10)."""
    doc = get_json(http, f"{tiles_url(collection)}?f=json")
    assert {ts["tileMatrixSetURI"] for ts in doc["tilesets"]} == {uri for _, uri in TMS.values()}
    for tileset in doc["tilesets"]:
        assert tileset["dataType"] == "map" and tileset["crs"]
        assert link(tileset, "self", "application/json")["href"].startswith(tiles_url(collection))
        assert link(tileset, f"{REL}tiling-scheme")


@pytest.mark.parametrize("collection", RASTERS)
@pytest.mark.parametrize("tms_id", TMS)
def test_tileset_metadata(http, collection, tms_id):
    """/req/tileset/description (Req 8 A-G)."""
    resp = http.get(f"{tiles_url(collection)}/{tms_id}", headers={"Accept": "application/json"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/json")
    md = resp.json()
    crs, tms_uri = TMS[tms_id]
    assert md["dataType"] == "map" and md["crs"] == crs and md["tileMatrixSetURI"] == tms_uri
    assert md["mediaTypes"] == ["image/png"]
    assert md["boundingBox"]["lowerLeft"] == RASTER_BBOX[:2] and md["boundingBox"]["upperRight"] == RASTER_BBOX[2:]
    item = link(md, "item")
    assert item["templated"] is True and item["type"] == "image/png"
    assert all(v in item["href"] for v in ("{tileMatrix}", "{tileRow}", "{tileCol}"))
    assert md["style"]["id"] == "default" and "transparent" in md["style"]["description"]
    limits = md["tileMatrixSetLimits"]
    assert limits[0]["tileMatrix"] == "0" and all(l["minTileRow"] <= l["maxTileRow"] for l in limits)


@pytest.mark.parametrize("tms_id", TMS)
def test_tiling_scheme_link_resolves_to_tms_definition(http, tms_id):
    md = get_json(http, f"{tiles_url('synthetic-worldpop-2001')}/{tms_id}?f=json")
    tms = get_json(http, link(md, f"{REL}tiling-scheme")["href"])
    assert tms["id"] == tms_id and tms["uri"] == TMS[tms_id][1] and tms["crs"] == TMS[tms_id][0]
    assert len(tms["tileMatrices"]) > 13


def test_tilematrixsets_listing(http):
    listing = get_json(http, f"{OGC}/TileMatrixSets?f=json")
    assert {"WebMercatorQuad", "WorldCRS84Quad"} <= {t["id"] for t in listing["tileMatrixSets"]}


def fetch_tile(http, collection, tms_id, lon, lat, zoom=ZOOM, **kw):
    tile, pixel = tile_pixel(tms_id, lon, lat, zoom)
    md = get_json(http, f"{tiles_url(collection)}/{tms_id}?f=json")
    href = (link(md, "item")["href"].replace("{tileMatrix}", str(tile.z))
            .replace("{tileRow}", str(tile.y)).replace("{tileCol}", str(tile.x)))
    return http.get(href, **kw), pixel


@pytest.mark.parametrize("collection", RASTERS)
@pytest.mark.parametrize("tms_id", TMS)
def test_tile_image_renders_valid_cells_and_masks_nodata(http, collection, tms_id):
    """/req/core/tc-success (Req 5 A-C) and /req/png/content (Req 26)."""
    resp, pixel = fetch_tile(http, collection, tms_id, *VALID)
    image = png_image(resp)
    assert image.size == (256, 256)
    assert image.getpixel(pixel)[3] == 255
    for lonlat in (NODATA_COL, NODATA_ROW):
        resp, pixel = fetch_tile(http, collection, tms_id, *lonlat)
        assert png_image(resp).getpixel(pixel)[3] == 0


def test_same_portrayal_rule_across_tiles(http):
    """/req/png/content D: adjacent tiles of one tileset use the same colours."""
    colours = set()
    for lon in (78.010, 78.050):  # two different z13 tile columns
        resp, pixel = fetch_tile(http, "synthetic-worldpop-2001", "WebMercatorQuad", lon, 17.970)
        colours.add(png_image(resp).getpixel(pixel))
    assert len(colours) == 1


def test_tile_selected_by_content_negotiation(http):
    tile, _ = tile_pixel("WebMercatorQuad", *VALID, ZOOM)
    url = f"{tiles_url('synthetic-worldpop-2011')}/WebMercatorQuad/{tile.z}/{tile.y}/{tile.x}"
    assert png_image(http.get(url, headers={"Accept": "image/png"})).size == (256, 256)
    not_acceptable = http.get(url, headers={"Accept": "image/jpeg"})
    assert not_acceptable.status_code == 406 and not_acceptable.headers["content-type"].startswith("application/json")
    assert http.get(url, params={"f": "jpeg"}).status_code == 400


@pytest.mark.parametrize("path", [
    "WebMercatorQuad/11/0/0",          # inside the TMS, outside tileMatrixSetLimits
    "WebMercatorQuad/30/0/0",          # no such tile matrix
    "WebMercatorQuad/11/99999/1467",   # row beyond matrixHeight
    "WebMercatorQuad/11/abc/1467",
    "WorldCRS84Quad/0/5/5",
    "NoSuchTMS/11/919/1467",
])
def test_out_of_range_tiles_are_404(http, path):
    """/req/core/tc-error A (Req 6)."""
    resp = http.get(f"{tiles_url('synthetic-worldpop-2001')}/{path}", params={"f": "png"})
    assert resp.status_code in (400, 404)
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["code"]


def test_unknown_tileset_and_collection(http):
    assert http.get(f"{tiles_url('synthetic-worldpop-2001')}/NoSuchTMS", params={"f": "json"}).status_code == 404
    assert http.get(tiles_url("no-such-collection"), params={"f": "json"}).status_code == 404


def test_vector_collection_has_no_tiles(http):
    assert http.get(tiles_url("synthetic-towns"), params={"f": "json"}).status_code in (400, 404)


def test_tilejson_alternate_for_webmercator_only(http):
    tj = get_json(http, f"{tiles_url('synthetic-worldpop-2001')}/WebMercatorQuad?f=tilejson")
    assert tj["tilejson"] == "3.0.0" and tj["bounds"] == RASTER_BBOX
    assert tj["tiles"][0].startswith(f"{tiles_url('synthetic-worldpop-2001')}/WebMercatorQuad/")
    assert http.get(f"{tiles_url('synthetic-worldpop-2001')}/WorldCRS84Quad", params={"f": "tilejson"}).status_code == 400


def test_html_tiles_viewer_renders_map_tiles(http):
    resp = http.get(tiles_url("synthetic-worldpop-2001"), params={"f": "html"})
    assert resp.status_code == 200 and "L.TileLayer" in resp.text
