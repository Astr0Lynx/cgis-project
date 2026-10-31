"""OGC API - Features through the gateway (TEST_PLAN 4; ACCEPTANCE P0 6)."""

from __future__ import annotations

import pytest

from conftest import OGC, PUBLIC, RASTER_2001, RASTER_2011, TOWNS, internal

CORE = "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core"
GEOJSON = "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson"
OAS30 = "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30"
CRS_EXT = "http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/crs"
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
EPSG4326 = "http://www.opengis.net/def/crs/EPSG/0/4326"


def items(http, **params):
    params.setdefault("f", "json")
    resp = http.get(f"{OGC}/collections/{TOWNS}/items", params=params)
    return resp


def test_landing_page_links(http):
    resp = http.get(f"{OGC}/", params={"f": "json"})
    assert resp.status_code == 200
    rels = {link["rel"] for link in resp.json()["links"]}
    assert {"service-desc", "conformance", "data"} <= rels


def test_conformance_declares_features_and_tiles_but_not_coverages(http):
    conf = http.get(f"{OGC}/conformance", params={"f": "json"}).json()["conformsTo"]
    assert {CORE, GEOJSON, OAS30} <= set(conf)
    assert CRS_EXT in conf
    assert "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core" in conf
    assert not [c for c in conf if "ogcapi-coverages" in c], "Coverages is a draft and must not be claimed"


def test_collections_are_the_ready_catalog_datasets(http):
    cols = {c["id"]: c for c in http.get(f"{OGC}/collections", params={"f": "json"}).json()["collections"]}
    assert {TOWNS, RASTER_2001, RASTER_2011} <= set(cols)
    towns = cols[TOWNS]
    assert any(link["rel"] == "items" for link in towns["links"])
    assert CRS84 in towns.get("crs", [CRS84])
    assert "SYNTHETIC" in towns["title"]


def test_items_return_town_a_and_b_as_geojson(http):
    resp = items(http)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/geo+json")
    fc = resp.json()
    assert fc["type"] == "FeatureCollection"
    by_id = {f["id"]: f for f in fc["features"]}
    assert set(by_id) == {"town-a", "town-b"}
    assert by_id["town-a"]["properties"]["name"] == "Town A"
    assert by_id["town-b"]["properties"]["name"] == "Town B"
    assert by_id["town-a"]["geometry"]["type"] in ("MultiPolygon", "Polygon")
    assert fc["numberReturned"] == 2


def test_item_by_id_and_missing_item(http):
    resp = http.get(f"{OGC}/collections/{TOWNS}/items/town-a", params={"f": "json"})
    assert resp.status_code == 200 and resp.json()["id"] == "town-a"
    assert http.get(f"{OGC}/collections/{TOWNS}/items/no-such-town", params={"f": "json"}).status_code == 404
    assert http.get(f"{OGC}/collections/no-such-collection/items", params={"f": "json"}).status_code == 404


@pytest.mark.parametrize("bbox,expected", [
    ("78.009,17.979,78.021,17.991", {"town-a"}),
    ("78.029,17.949,78.036,17.956", {"town-b"}),
    ("78.000,17.936,78.064,18.000", {"town-a", "town-b"}),
    ("79.0,19.0,79.1,19.1", set()),
])
def test_bbox_filter(http, bbox, expected):
    resp = items(http, bbox=bbox)
    assert resp.status_code == 200
    assert {f["id"] for f in resp.json()["features"]} == expected


def test_invalid_bbox_is_rejected(http):
    assert items(http, bbox="1,2,3").status_code == 400


def test_paging_follows_next_link(http):
    page1 = items(http, limit=1).json()
    assert page1["numberReturned"] == 1
    nxt = [link["href"] for link in page1["links"] if link["rel"] == "next"]
    assert nxt, "first page of 2 features must link to the next page"
    page2 = http.get(internal(nxt[0])).json()
    ids = {page1["features"][0]["id"], page2["features"][0]["id"]}
    assert ids == {"town-a", "town-b"}


def test_default_response_crs_is_crs84(http):
    resp = items(http)
    if "content-crs" in resp.headers:
        assert resp.headers["content-crs"].strip("<>") == CRS84
    coords = resp.json()["features"][0]["geometry"]["coordinates"]
    first = coords[0][0][0] if resp.json()["features"][0]["geometry"]["type"] == "MultiPolygon" else coords[0][0]
    assert 78 <= first[0] <= 78.1 and 17.9 <= first[1] <= 18.0   # lon, lat


def test_epsg4326_response_crs_uses_lat_lon_axis_order(http):
    resp = http.get(f"{OGC}/collections/{TOWNS}/items/town-a", params={"f": "json", "crs": EPSG4326})
    assert resp.status_code == 200
    assert resp.headers["content-crs"].strip("<>") == EPSG4326
    geom = resp.json()["geometry"]
    ring = geom["coordinates"][0][0] if geom["type"] == "MultiPolygon" else geom["coordinates"][0]
    assert all(17.9 <= lat <= 18.0 and 78.0 <= lon <= 78.1 for lat, lon in ring)


def test_links_use_public_gateway_prefix(http):
    doc = http.get(f"{OGC}/collections/{TOWNS}", params={"f": "json"}).json()
    hrefs = [link["href"] for link in doc["links"] if link["href"].startswith("http")]
    assert hrefs and all(h.startswith(f"{PUBLIC}/ogc") or h.startswith(f"{PUBLIC}/tiles") for h in hrefs), hrefs


def test_openapi_document(http):
    resp = http.get(f"{OGC}/openapi", params={"f": "json"})
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["openapi"].startswith("3.")
    assert f"/collections/{TOWNS}/items" in doc["paths"]


def test_html_landing_page_marks_synthetic(http):
    resp = http.get(f"{OGC}/", params={"f": "html"})
    assert resp.status_code == 200 and "SYNTHETIC" in resp.text
