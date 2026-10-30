"""Request validation and structured 4xx errors."""

from __future__ import annotations

import json

import pytest

from synthetic import TOWN_A, rect

ZONAL = "/operations/zonal-statistics"
CLIP = "/operations/clip"


def body(**overrides):
    payload = {"dataset_id": "synthetic-worldpop-2001", "aoi": TOWN_A, "aoi_crs": "EPSG:4326"}
    payload.update(overrides)
    return payload


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    err = response.json()["error"]
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    return err


BOWTIE = {"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.99], [78.02, 17.98], [78.01, 17.99], [78.01, 17.98]]]}


@pytest.mark.parametrize(
    "aoi, code",
    [
        ({"type": "Polygon", "coordinates": []}, "EMPTY_GEOMETRY"),
        ({"type": "MultiPolygon", "coordinates": [[]]}, "EMPTY_GEOMETRY"),
        ({"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.01, 17.98]]]}, "INVALID_GEOMETRY"),
        ({"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.02, 17.99], [78.01, 17.99]]]}, "INVALID_GEOMETRY"),
        (BOWTIE, "INVALID_GEOMETRY"),
        ({"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.03, 17.98], [78.01, 17.98]]]}, "INVALID_GEOMETRY"),
        ({"type": "Point", "coordinates": [78.01, 17.98]}, "UNSUPPORTED_GEOMETRY_TYPE"),
        ({"type": "GeometryCollection", "geometries": []}, "UNSUPPORTED_GEOMETRY_TYPE"),
        ({"type": "Feature", "geometry": None}, "EMPTY_GEOMETRY"),
        ({"type": "Polygon", "coordinates": [[["a", 1], [1, 2], [2, 2], ["a", 1]]]}, "INVALID_GEOMETRY"),
        (rect(78.01, 95.0, 78.02, 96.0), "COORDINATES_OUT_OF_RANGE"),
        (rect(17.98, 78.01, 17.99, 78.02), "AOI_OUTSIDE_RASTER"),  # swapped lat/lon order
    ],
    ids=["empty", "empty-multi", "short-ring", "unclosed", "self-intersecting", "zero-area",
         "point", "collection", "null-feature", "non-numeric", "lat-out-of-range", "swapped-axes"],
)
def test_bad_geometries(client, aoi, code):
    r = client.post(ZONAL, json=body(aoi=aoi))
    err = assert_error(r, 422, code)
    if aoi is BOWTIE:
        assert "Self-intersection" in err["details"]["reason"]


def test_non_finite_coordinates(client):
    raw = json.dumps(body()).replace("78.01, 17.98], [78.02", "NaN, 17.98], [78.02", 1)
    assert "NaN" in raw
    r = client.post(ZONAL, content=raw, headers={"content-type": "application/json"})
    assert_error(r, 422, "NON_FINITE_COORDINATES")
    raw_inf = json.dumps(body()).replace("17.99]", "Infinity]", 1)
    r = client.post(ZONAL, content=raw_inf, headers={"content-type": "application/json"})
    assert_error(r, 422, "NON_FINITE_COORDINATES")


@pytest.mark.parametrize(
    "crs", ["EPSG:999999", "foo", "+proj=longlat +datum=WGS84", "EPSG:4978", "EPSG:5773", "EPSG:4979",
            "https://example.com/crs", ""]
)
def test_unsupported_crs(client, crs):
    assert_error(client.post(ZONAL, json=body(aoi_crs=crs)), 422, "UNSUPPORTED_CRS")


def test_unsupported_output_crs(client):
    assert_error(client.post(CLIP, json=body(output_crs="EPSG:0")), 422, "UNSUPPORTED_CRS")


def test_unknown_dataset(client):
    assert_error(client.post(ZONAL, json=body(dataset_id="no-such-dataset")), 404, "DATASET_NOT_FOUND")


@pytest.mark.parametrize("dataset_id", ["staged-only", "failed-only"])
def test_not_ready_dataset(client, dataset_id):
    err = assert_error(client.post(ZONAL, json=body(dataset_id=dataset_id)), 409, "DATASET_NOT_READY")
    assert err["details"]["status"] in ("staged", "failed")


def test_vector_dataset_rejected(client):
    assert_error(client.post(CLIP, json=body(dataset_id="synthetic-towns")), 422, "DATASET_NOT_RASTER")


@pytest.mark.parametrize(
    "dataset_id",
    ["../synthetic_2001.tif", "/data/assets/synthetic_2001.tif", "https://example.com/x.tif",
     "file:///etc/passwd", "/vsicurl/http://x", "a b", ""],
)
def test_dataset_id_must_be_a_slug(client, dataset_id):
    r = client.post(ZONAL, json=body(dataset_id=dataset_id))
    assert_error(r, 422, "INVALID_DATASET_ID")


@pytest.mark.parametrize("field", ["asset_path", "path", "url", "file"])
def test_client_cannot_supply_paths_or_urls(client, field):
    r = client.post(CLIP, json=body(**{field: "/etc/passwd"}))
    err = assert_error(r, 422, "INVALID_REQUEST")
    assert any(p["type"] == "extra_forbidden" for p in err["details"]["problems"])
    assert "/etc/passwd" not in r.text  # submitted values are not echoed


def test_missing_aoi_crs(client):
    payload = body()
    del payload["aoi_crs"]
    assert_error(client.post(ZONAL, json=payload), 422, "INVALID_REQUEST")


def test_malformed_json(client):
    r = client.post(ZONAL, content="{not json", headers={"content-type": "application/json"})
    assert_error(r, 422, "INVALID_REQUEST")


@pytest.mark.parametrize("fmt", ["PNG", "NetCDF", "../x", ""])
def test_unsupported_format(client, fmt):
    assert_error(client.post(CLIP, json=body(format=fmt)), 422, "UNSUPPORTED_FORMAT")


@pytest.mark.parametrize("ops", [["median"], [], ["sum", "drop table"]])
def test_unsupported_operation(client, ops):
    assert_error(client.post(ZONAL, json=body(operations=ops)), 422, "UNSUPPORTED_OPERATION")


def test_aoi_without_any_pixel_center(client):
    # 0.0004 x 0.0004 deg square placed between pixel centers
    assert_error(client.post(ZONAL, json=body(aoi=rect(78.0101, 17.9801, 78.0104, 17.9804))), 422, "NO_PIXELS_SELECTED")


def test_multiband_rejected(client):
    assert_error(client.post(ZONAL, json=body(dataset_id="multiband", aoi=rect(78.0, 17.993, 78.008, 18.0))),
                 422, "UNSUPPORTED_OPERATION")


def test_vertex_limit(make_client):
    c, _, _ = make_client(max_aoi_vertices=4)
    assert_error(c.post(ZONAL, json=body()), 413, "AOI_TOO_COMPLEX")


def test_part_limit(make_client):
    c, _, _ = make_client(max_aoi_parts=1)
    multi = {"type": "MultiPolygon", "coordinates": [TOWN_A["coordinates"], TOWN_A["coordinates"]]}
    assert_error(c.post(ZONAL, json=body(aoi=multi)), 413, "AOI_TOO_COMPLEX")


def test_window_pixel_limit(make_client):
    c, _, _ = make_client(max_window_pixels=50)
    assert_error(c.post(ZONAL, json=body()), 413, "AOI_TOO_LARGE")


def test_output_size_limit(make_client):
    c, _, _ = make_client(max_output_bytes=100)
    assert_error(c.post(CLIP, json=body()), 413, "OUTPUT_TOO_LARGE")


def test_request_body_limit(make_client):
    c, _, _ = make_client(max_request_bytes=200)
    assert_error(c.post(ZONAL, json=body(padding="x" * 500)), 413, "REQUEST_TOO_LARGE")

    def chunks():  # no Content-Length (chunked transfer)
        yield b'{"dataset_id": "' + b"x" * 300 + b'"}'

    r = c.post(ZONAL, content=chunks(), headers={"content-type": "application/json"})
    assert_error(r, 413, "REQUEST_TOO_LARGE")


def test_unknown_route_is_structured(client):
    assert_error(client.get("/nope"), 404, "NOT_FOUND")
    assert_error(client.get("/operations/clip"), 422, "INVALID_REQUEST")  # 'clip' is not a UUID
    assert_error(client.delete("/operations/zonal-statistics"), 405, "METHOD_NOT_ALLOWED")
