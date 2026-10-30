"""OGC API - Features (Part 1 Core, Part 2 CRS, Part 3 queryables/CQL2 subset) over HTTP."""

import pytest
from conftest import CRS84, EPSG4326, OGC, RASTERS, TOWNS, get_json, link
from openapi_spec_validator import validate

ITEMS = f"{OGC}/collections/{TOWNS}/items"
TOWN_A_RING = [[78.01, 17.98], [78.01, 17.99], [78.02, 17.99], [78.02, 17.98], [78.01, 17.98]]

EXPECTED_CONFORMANCE = {
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/landing-page",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/json",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/html",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/html",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
    "http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/crs",
    "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/queryables",
    "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/queryables-query-parameters",
    "http://www.opengis.net/spec/cql2/1.0/conf/cql2-text",
    "http://www.opengis.net/spec/cql2/1.0/conf/basic-cql2",
    "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tileset",
    "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/tilesets-list",
    "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/geodata-tilesets",
    "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/png",
}


def items(http, **params):
    return get_json(http, ITEMS, params={"f": "json", **params})


def test_landing_page_links(http):
    landing = get_json(http, f"{OGC}/?f=json")
    for rel in ("self", "service-desc", "service-doc", "conformance", "data"):
        assert link(landing, rel)["href"].startswith(OGC)
    assert link(landing, "http://www.opengis.net/def/rel/ogc/1.0/tiling-schemes")


def test_conformance_declares_exactly_what_is_supported(http):
    declared = set(get_json(http, f"{OGC}/conformance?f=json")["conformsTo"])
    assert declared == EXPECTED_CONFORMANCE
    drafts = ("ogcapi-features-4", "ogcapi-common-2", "ogcapi-common-3")
    assert not any(d in c for d in drafts for c in declared) and not any(c.endswith("/conf/mvt") for c in declared)


def test_collections_list_catalog_datasets(http):
    collections = {c["id"]: c for c in get_json(http, f"{OGC}/collections?f=json")["collections"]}
    assert {TOWNS, *RASTERS} <= set(collections)
    towns = collections[TOWNS]
    assert link(towns, "items", "application/geo+json")
    assert towns["extent"]["spatial"]["bbox"] == [[78.01, 17.95, 78.035, 17.99]]
    assert towns["extent"]["spatial"]["crs"] == CRS84
    assert CRS84 in towns["crs"] and EPSG4326 in towns["crs"]
    assert towns["storageCrs"] == CRS84


def test_collection_metadata(http):
    towns = get_json(http, f"{OGC}/collections/{TOWNS}?f=json")
    assert towns["id"] == TOWNS and "SYNTHETIC" in towns["title"]
    assert link(towns, "self")["href"].startswith(f"{OGC}/collections/{TOWNS}")


def test_items_feature_collection(http):
    resp = http.get(ITEMS, params={"f": "json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/geo+json")
    assert resp.headers["content-crs"] == f"<{CRS84}>"
    fc = resp.json()
    assert fc["type"] == "FeatureCollection"
    assert fc["numberMatched"] == 2 and fc["numberReturned"] == 2
    by_id = {f["id"]: f for f in fc["features"]}
    assert set(by_id) == {"town-a", "town-b"}
    town_a = by_id["town-a"]
    assert town_a["type"] == "Feature"
    assert town_a["properties"] == {"unit_id": "town-a", "name": "Town A", "level": "synthetic_town"}
    assert town_a["geometry"]["type"] == "MultiPolygon"
    assert sorted(map(tuple, town_a["geometry"]["coordinates"][0][0])) == sorted(map(tuple, TOWN_A_RING))


def test_item_by_id(http):
    resp = http.get(f"{ITEMS}/town-b", params={"f": "json"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/geo+json")
    feature = resp.json()
    assert feature["id"] == "town-b" and feature["properties"]["name"] == "Town B"
    assert link(feature, "collection")


@pytest.mark.parametrize("url", [f"{ITEMS}/no-such-town", f"{OGC}/collections/no-such-collection/items",
                                 f"{OGC}/collections/no-such-collection"])
def test_unknown_resources_return_404(http, url):
    resp = http.get(url, params={"f": "json"})
    assert resp.status_code == 404
    assert resp.json()["code"] in {"NotFound", "InvalidParameterValue"}


def test_bbox_filter_selects_town_a(http):
    fc = items(http, bbox="78.009,17.979,78.021,17.991")
    assert [f["id"] for f in fc["features"]] == ["town-a"]


def test_bbox_filter_selects_town_b(http):
    fc = items(http, bbox="78.029,17.949,78.036,17.956")
    assert [f["id"] for f in fc["features"]] == ["town-b"]


def test_disjoint_bbox_returns_empty_collection(http):
    fc = items(http, bbox="0,0,1,1")
    assert fc["numberMatched"] == 0 and fc["features"] == []


@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "78.1,17.9"])
def test_invalid_bbox_is_400(http, bbox):
    assert http.get(ITEMS, params={"f": "json", "bbox": bbox}).status_code == 400


def test_pagination_with_next_links(http):
    first = items(http, limit=1)
    assert first["numberReturned"] == 1 and first["numberMatched"] == 2
    second = get_json(http, link(first, "next")["href"])
    assert second["numberReturned"] == 1
    assert {first["features"][0]["id"], second["features"][0]["id"]} == {"town-a", "town-b"}
    assert link(second, "prev")


def test_limit_validation(http):
    assert http.get(ITEMS, params={"f": "json", "limit": "abc"}).status_code == 400
    capped = items(http, limit=100000)  # over max_items: throttled, not an error
    assert capped["numberReturned"] == 2


def test_crs_epsg4326_uses_lat_lon_axis_order(http):
    resp = http.get(f"{ITEMS}/town-a", params={"f": "json", "crs": EPSG4326})
    assert resp.status_code == 200
    assert resp.headers["content-crs"] == f"<{EPSG4326}>"
    ring = resp.json()["geometry"]["coordinates"][0][0]
    assert sorted(map(tuple, ring)) == sorted((lat, lon) for lon, lat in TOWN_A_RING)


def test_bbox_crs_epsg4326(http):
    fc = items(http, bbox="17.979,78.009,17.991,78.021", **{"bbox-crs": EPSG4326})
    assert [f["id"] for f in fc["features"]] == ["town-a"]


def test_unsupported_crs_is_400(http):
    assert http.get(ITEMS, params={"f": "json", "crs": "http://www.opengis.net/def/crs/EPSG/0/9999"}).status_code == 400


def test_datetime_matches_features_without_temporal_properties(http):
    for value in ("2001-01-01T00:00:00Z", "2001-01-01/..", "../2011-12-31T00:00:00Z"):
        assert items(http, datetime=value)["numberMatched"] == 2
    assert http.get(ITEMS, params={"f": "json", "datetime": "not-a-date"}).status_code == 400


def test_queryables(http):
    schema = get_json(http, f"{OGC}/collections/{TOWNS}/queryables?f=json")
    assert {"unit_id", "name", "level"} <= set(schema["properties"])


def test_queryable_as_query_parameter(http):
    assert [f["id"] for f in items(http, name="Town B")["features"]] == ["town-b"]


@pytest.mark.parametrize("expression,expected", [
    ("name = 'Town A'", ["town-a"]),
    ("name <> 'Town A'", ["town-b"]),
    ("unit_id > 'town-a'", ["town-b"]),
    ("name = 'Town A' OR name = 'Town B'", ["town-a", "town-b"]),
    ("level = 'synthetic_town' AND NOT (name = 'Town B')", ["town-a"]),
    ("level IS NULL", []),
])
def test_cql2_text_filter(http, expression, expected):
    fc = items(http, filter=expression, **{"filter-lang": "cql2-text"})
    assert sorted(f["id"] for f in fc["features"]) == expected


def test_html_representation(http):
    resp = http.get(ITEMS, params={"f": "html"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")


def test_openapi_document_is_valid_and_complete(http):
    resp = http.get(f"{OGC}/openapi", params={"f": "json"})
    assert resp.status_code == 200
    spec = resp.json()
    validate(spec)
    paths = spec["paths"]
    assert f"/collections/{TOWNS}/items" in paths and f"/collections/{TOWNS}/items/{{featureId}}" in paths
    item_params = paths[f"/collections/{TOWNS}/items"]["get"]["parameters"]
    remote_refs = [p["$ref"] for p in item_params if "$ref" in p and not p["$ref"].startswith("#/")]
    assert remote_refs == [], "items parameters must resolve without fetching remote documents"
    datetime_param = next(p for p in item_params if p.get("name") == "datetime")
    assert datetime_param == {**datetime_param, "in": "query", "required": False, "style": "form", "explode": False}
    for raster in RASTERS:
        base = f"/collections/{raster}/tiles"
        ops = {p: paths[p]["get"]["operationId"] for p in
               (base, f"{base}/{{tileMatrixSetId}}", f"{base}/{{tileMatrixSetId}}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}")}
        suffixes = sorted(op.split(".", 1)[1] for op in ops.values())
        assert suffixes == ["collection.map.getTile", "collection.map.getTileSet", "collection.map.getTileSetsList"]
