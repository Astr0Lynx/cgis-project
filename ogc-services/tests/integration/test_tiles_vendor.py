"""Vendor-specific TiTiler routes under /tiles/ and their access restrictions."""

import psycopg
import pytest
from conftest import (
    OGC,
    RASTER_BBOX,
    RASTERS,
    TILES,
    asset_path_of,
    collection_ids,
    get_json,
    png_image,
    tile_pixel,
    wait_for,
)


def test_health(http):
    assert get_json(http, f"{TILES}/healthz") == {"status": "ok"}
    assert get_json(http, f"{TILES}/readyz") == {"status": "ready"}


def test_dataset_listing_contains_only_ready_rasters(http):
    listing = get_json(http, f"{TILES}/datasets")
    ids = {d["dataset_id"] for d in listing["datasets"]}
    assert set(RASTERS) <= ids and "synthetic-towns" not in ids
    assert "not an OGC API" in listing["note"]


@pytest.mark.parametrize("dataset_id", RASTERS)
def test_tilejson_references_registered_raster(http, dataset_id):
    tj = get_json(http, f"{TILES}/datasets/{dataset_id}/WebMercatorQuad/tilejson.json")
    assert tj["bounds"] == pytest.approx(RASTER_BBOX)
    assert tj["tiles"][0].startswith(f"{TILES}/datasets/{dataset_id}/tiles/WebMercatorQuad/")
    info = get_json(http, f"{TILES}/datasets/{dataset_id}/info")
    assert info["bounds"] == pytest.approx(RASTER_BBOX)
    assert info["nodata_value"] == -9999 and info["dtype"] == "float32"


@pytest.mark.parametrize("dataset_id,value", RASTERS.items())
def test_point_values_match_fixture(http, dataset_id, value):
    assert get_json(http, f"{TILES}/datasets/{dataset_id}/point/78.015,17.985")["values"] == [value]
    assert get_json(http, f"{TILES}/datasets/{dataset_id}/point/78.001,17.999")["values"] == [None]


def test_vendor_tiles_render_both_years_on_a_shared_scale(http):
    tile, pixel = tile_pixel("WebMercatorQuad", 78.030, 17.970, 13)
    colours = {}
    for dataset_id in RASTERS:
        resp = http.get(f"{TILES}/datasets/{dataset_id}/tiles/WebMercatorQuad/{tile.z}/{tile.x}/{tile.y}.png",
                        params={"rescale": "0,2", "colormap_name": "viridis"})
        colours[dataset_id] = png_image(resp).getpixel(pixel)
    assert all(c[3] == 255 for c in colours.values())
    assert colours["synthetic-worldpop-2001"] != colours["synthetic-worldpop-2011"]


def test_no_url_parameter_anywhere_in_api(http):
    spec = get_json(http, f"{TILES}/openapi.json")
    names = {p.get("name") for ops in spec["paths"].values() for op in ops.values()
             for p in op.get("parameters", []) if isinstance(op, dict)}
    assert "url" not in names
    assert not any(p.startswith(("/cog", "/stac", "/mosaic", "/mosaicjson")) for p in spec["paths"])


@pytest.mark.parametrize("path", ["/cog/info?url=/etc/passwd", "/cog/tiles/WebMercatorQuad/0/0/0?url=file:///etc/passwd",
                                  "/stac/info?url=http://169.254.169.254/latest/meta-data/"])
def test_stock_titiler_url_endpoints_do_not_exist(http, path):
    resp = http.get(f"{TILES}{path}")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found"


def test_url_query_parameter_is_ignored(http):
    info = get_json(http, f"{TILES}/datasets/synthetic-worldpop-2001/info", params={"url": "/etc/passwd"})
    assert info["bounds"] == pytest.approx(RASTER_BBOX)


@pytest.mark.parametrize("dataset_id", ["no-such-dataset", "synthetic-towns", "UPPER.case_ok-but-missing"])
def test_unknown_or_non_raster_dataset(http, dataset_id):
    resp = http.get(f"{TILES}/datasets/{dataset_id}/info")
    assert resp.status_code == 404
    assert resp.json()["error"] == {"code": "dataset_not_found", "message": "Unknown dataset"}


@pytest.mark.parametrize("raw", ["..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "a%00b", "-leading-dash"])
def test_malformed_dataset_ids(http, raw):
    # Refused by the gateway (400, e.g. NUL bytes) or by the tiler's id validation (404).
    resp = http.get(f"{TILES}/datasets/{raw}/info")
    assert resp.status_code in (400, 404)


@pytest.mark.parametrize("asset_path", [
    "../../../etc/passwd.tif",
    "/etc/passwd.tif",
    "hostile/escape.tif",              # symlink to /etc/hostname (created by the test stack)
    "hostile/disguised.tif",           # VRT XML with a .tif suffix (created by the test stack)
    "http://169.254.169.254/x.tif",
    "/vsicurl/http://example.com/x.tif",
    "synthetic/missing.tif",
])
def test_hostile_catalog_asset_paths_are_refused(http, temp_dataset, asset_path):
    # Integration note (Agent 6): on the integrated schema, Agent 2's CHECK constraint
    # datasets_asset_path_safe refuses absolute, traversal, URL and /vsi* paths at INSERT, so such a
    # row can never reach the tiler. That earlier refusal is accepted; paths the DB allows
    # (symlink escape, disguised VRT, missing file) are still checked against the tiler below.
    try:
        dataset_id = temp_dataset("raster", asset_path=asset_path)
    except psycopg.errors.CheckViolation as exc:
        assert exc.diag.constraint_name == "datasets_asset_path_safe", exc
        return
    resp = http.get(f"{TILES}/datasets/{dataset_id}/tiles/WebMercatorQuad/13/5871/3680.png")
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["code"] == "asset_unavailable"
    assert "/" not in body["message"] and "etc" not in body["message"]


def test_staged_and_failed_rasters_are_not_served(http, db, temp_dataset):
    for status in ("staged", "failed"):
        dataset_id = temp_dataset("raster", asset_path=asset_path_of(db, "synthetic-worldpop-2001"), status=status)
        assert http.get(f"{TILES}/datasets/{dataset_id}/info").status_code == 404


def test_ogc_tiles_for_a_refused_asset_are_404_not_blank(http, temp_dataset):
    dataset_id = temp_dataset("raster", asset_path="hostile/escape.tif")
    assert wait_for(lambda: dataset_id in collection_ids(http)), "publisher did not pick up the row"
    resp = http.get(f"{OGC}/collections/{dataset_id}/tiles/WebMercatorQuad/13/3680/5871", params={"f": "png"})
    assert resp.status_code == 404 and resp.headers["content-type"].startswith("application/json")
    metadata = http.get(f"{OGC}/collections/{dataset_id}/tiles/WebMercatorQuad", params={"f": "json"})
    assert metadata.status_code == 404
