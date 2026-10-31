"""Negative cases and mutation-path security (TEST_PLAN 8; P0 4, 9, 10)."""

from __future__ import annotations

import json

import pytest

from conftest import API, GATEWAY, OGC, RASTER_2001, STATE, TILES, TOWN_A, TOWNS, error_code, post_json

NEG = STATE / "neg"


def zonal(http, **payload):
    payload.setdefault("dataset_id", RASTER_2001)
    payload.setdefault("aoi", TOWN_A)
    payload.setdefault("aoi_crs", "EPSG:4326")
    return post_json(http, "/operations/zonal-statistics", payload)


BAD_AOIS = {
    "unclosed_ring": ({"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.02, 17.99], [78.01, 17.99]]]},
                      {"INVALID_GEOMETRY", "INVALID_REQUEST"}),
    "bowtie": ({"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.99], [78.02, 17.98], [78.01, 17.99], [78.01, 17.98]]]},
               {"INVALID_GEOMETRY"}),
    "empty": ({"type": "Polygon", "coordinates": []}, {"EMPTY_GEOMETRY", "INVALID_GEOMETRY", "INVALID_REQUEST"}),
    "point": ({"type": "Point", "coordinates": [78.015, 17.985]}, {"UNSUPPORTED_GEOMETRY_TYPE", "INVALID_REQUEST"}),
    "zero_area": ({"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.01, 17.98], [78.01, 17.98]]]},
                  {"INVALID_GEOMETRY", "EMPTY_GEOMETRY", "INVALID_REQUEST"}),
    "lat_out_of_range": ({"type": "Polygon", "coordinates": [[[78.0, 95.0], [78.1, 95.0], [78.1, 96.0], [78.0, 96.0], [78.0, 95.0]]]},
                         {"COORDINATES_OUT_OF_RANGE", "INVALID_GEOMETRY"}),
    "not_geojson": ("POLYGON((78 17, 78 18, 79 18, 78 17))", {"INVALID_REQUEST", "INVALID_GEOMETRY"}),
}


@pytest.mark.parametrize("name", sorted(BAD_AOIS))
def test_malformed_aoi_rejected_with_structured_error(http, name):
    aoi, codes = BAD_AOIS[name]
    resp = zonal(http, aoi=aoi)
    assert resp.status_code == 422, resp.text
    assert error_code(resp) in codes, resp.text


@pytest.mark.parametrize("crs", ["EPSG:999999", "EPSG:abc", "urn:x-foo:crs", ""])
def test_unsupported_crs(http, crs):
    resp = zonal(http, aoi_crs=crs)
    assert resp.status_code == 422 and error_code(resp) in ("UNSUPPORTED_CRS", "INVALID_REQUEST"), resp.text


def test_missing_aoi_crs_is_rejected(http):
    resp = post_json(http, "/operations/zonal-statistics", {"dataset_id": RASTER_2001, "aoi": TOWN_A})
    assert resp.status_code == 422 and error_code(resp)


def test_nonexistent_dataset(http):
    resp = zonal(http, dataset_id="no-such-dataset")
    assert resp.status_code == 404 and error_code(resp) == "DATASET_NOT_FOUND"


def test_vector_dataset_is_not_a_raster(http):
    resp = zonal(http, dataset_id=TOWNS)
    assert resp.status_code == 422 and error_code(resp) == "DATASET_NOT_RASTER"


@pytest.mark.parametrize("dataset_id", ["../../etc/passwd", "/data/assets/rasters", "http://example.com/x.tif",
                                        "synthetic-worldpop-2001'; DROP TABLE catalog.datasets;--"])
def test_dataset_id_injection_rejected(http, dataset_id):
    resp = zonal(http, dataset_id=dataset_id)
    assert resp.status_code in (404, 422) and error_code(resp) in ("INVALID_DATASET_ID", "DATASET_NOT_FOUND", "INVALID_REQUEST")


def test_unsupported_export_format(http):
    resp = post_json(http, "/operations/clip", {"dataset_id": RASTER_2001, "aoi": TOWN_A, "aoi_crs": "EPSG:4326",
                                                "format": "PNG"})
    assert resp.status_code == 422 and error_code(resp) == "UNSUPPORTED_FORMAT"


def test_out_of_bounds_clip(http):
    aoi = {"type": "Polygon", "coordinates": [[[10, 10], [10.1, 10], [10.1, 10.1], [10, 10.1], [10, 10]]]}
    resp = post_json(http, "/operations/clip", {"dataset_id": RASTER_2001, "aoi": aoi, "aoi_crs": "EPSG:4326"})
    assert resp.status_code == 422 and error_code(resp) == "AOI_OUTSIDE_RASTER"


def test_unknown_and_malformed_job_ids(http):
    unknown = http.get(f"{API}/operations/00000000-0000-4000-8000-000000000000")
    assert unknown.status_code == 404 and error_code(unknown) == "JOB_NOT_FOUND"
    assert http.get(f"{API}/operations/not-a-uuid").status_code == 422
    assert http.get(f"{API}/downloads/00000000-0000-4000-8000-000000000000").status_code == 404
    assert http.get(f"{API}/downloads/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404, 422)


def test_oversized_request_rejected(http):
    big = {"type": "Polygon", "coordinates": [[[78.01, 17.98]] * 70000]}
    resp = post_json(http, "/operations/zonal-statistics", {"dataset_id": RASTER_2001, "aoi": big, "aoi_crs": "EPSG:4326"})
    assert resp.status_code == 413


def test_unexpected_fields_rejected(http):
    resp = zonal(http, asset_path="/etc/passwd")
    assert resp.status_code == 422 and error_code(resp)


# --- no unauthenticated mutation path -------------------------------------------------------------

@pytest.mark.parametrize("method,url", [
    ("POST", f"{OGC}/collections/{TOWNS}/items"),
    ("PUT", f"{OGC}/collections/{TOWNS}/items/town-a"),
    ("DELETE", f"{OGC}/collections/{TOWNS}/items/town-a"),
    ("PATCH", f"{OGC}/collections/{TOWNS}"),
    ("POST", f"{TILES}/datasets"),
    ("DELETE", f"{TILES}/datasets/{RASTER_2001}"),
    ("POST", f"{GATEWAY}/viewer/index.html"),
])
def test_read_only_prefixes_refuse_writes(http, method, url):
    assert http.request(method, url, content=b"{}").status_code == 403


def test_processing_api_has_no_catalog_mutation_routes(http):
    paths = http.get(f"{API}/openapi.json").json()["paths"]
    allowed = {"/operations/clip", "/operations/zonal-statistics", "/operations/{job_id}", "/downloads/{job_id}",
               "/downloads/{job_id}/metadata", "/health", "/ready"}
    assert set(paths) <= allowed, set(paths) - allowed
    for path, ops in paths.items():
        assert set(ops) <= {"get", "post"}, (path, ops)
        if "post" in ops:
            assert path in ("/operations/clip", "/operations/zonal-statistics")
    for method, url in (("DELETE", f"{API}/operations/00000000-0000-4000-8000-000000000000"),
                        ("POST", f"{API}/datasets"), ("POST", f"{API}/import"), ("PUT", f"{API}/operations/clip")):
        assert http.request(method, url, content=b"{}").status_code in (404, 405)


def test_database_has_no_host_port():
    path = STATE / "host_checks.json"
    if not path.exists():
        pytest.skip("written by run_e2e.sh")
    checks = json.loads(path.read_text())
    assert checks["db_published_port"] == ""


# --- negative ingestion (imports run by run_e2e.sh with the real CLI) ------------------------------

def neg_result(case):
    exit_file, out_file = NEG / f"{case}.exit", NEG / f"{case}.stdout"
    if not exit_file.exists():
        pytest.skip("negative imports are run by run_e2e.sh")
    lines = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip().startswith("{")]
    return int(exit_file.read_text().strip()), lines


@pytest.mark.negative_ingest
@pytest.mark.parametrize("case,codes", [
    ("corrupt_tiff", None),
    ("missing_crs", None),
    ("unsupported_crs", None),
    ("conflicting_reimport", None),
    ("invalid_geometry", None),
    ("missing_file", None),
])
def test_bad_import_fails_clearly(case, codes):
    code, lines = neg_result(case)
    assert code != 0, f"{case} import should fail"
    failures = [line for line in lines if line.get("outcome") == "failed"]
    assert failures, lines
    err = failures[-1]["error"]
    assert err.get("code") and err.get("message"), err


@pytest.mark.negative_ingest
def test_bad_imports_did_not_corrupt_ready_datasets(db):
    neg_result("conflicting_reimport")
    row = db.execute("SELECT status, checksum_sha256 FROM catalog.datasets WHERE dataset_id=%s AND version=1",
                     (RASTER_2001,)).fetchone()
    assert row[0] == "ready"
    seeded = [json.loads(line) for line in (STATE / "seed1.jsonl").read_text().splitlines()
              if line.startswith("{") and RASTER_2001 in line]
    assert row[1] == seeded[-1]["checksum_sha256"]   # checksum recorded by the first seed
    current = {r[0] for r in db.execute("SELECT dataset_id FROM catalog.current_datasets").fetchall()}
    assert not {d for d in current if d.startswith("e2e-neg-")}
    assert db.execute("SELECT count(*) FROM geo.admin_units WHERE unit_id = 'e2e-bowtie'").fetchone()[0] == 0


@pytest.mark.negative_ingest
def test_conflicting_import_left_values_intact(http):
    neg_result("conflicting_reimport")
    resp = zonal(http)
    assert resp.json()["sum"] == 100
