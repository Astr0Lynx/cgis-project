# Unit semantics, health/readiness, the OpenAPI document and gateway prefix handling.

from __future__ import annotations

import pytest

from spatial_processing.raster import classify_units
from synthetic import ZONAL, payload


# POST a Town A zonal-statistics request for `dataset_id`.
def zonal(client, dataset_id, **extra):
    return client.post(ZONAL, json=payload(dataset_id, **extra))


@pytest.mark.parametrize(
    "units, kind",
    [
        ("people_per_cell", "count"),
        ("count", "count"),
        ("people_per_km2", "density"),
        ("persons per sq km", "density"),
        ("people/km^2", "density"),
        ("people_per_hectare", "density"),
        ("population density", "density"),
        ("index_value", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
# Units are classified as count, density or unknown.
def test_classify_units(units, kind):
    assert classify_units(units) == kind


# Density units: sum omitted with a warning by default; mean and count still reported.
def test_density_sum_is_omitted_by_default(client):
    body = zonal(client, "density").json()
    assert body["units"] == "people_per_km2"
    assert body["sum"] is None
    assert body["sum_semantics"] == "not_applicable_density"
    assert body["mean"] == pytest.approx(5)
    assert body["count_valid"] == 100
    assert any("density" in w for w in body["warnings"])


# Density units: an explicit sum gives 422 UNIT_SEMANTICS.
def test_density_sum_explicitly_requested_is_rejected(client):
    r = zonal(client, "density", operations=["sum", "mean"])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNIT_SEMANTICS"


# Density units: mean alone is allowed.
def test_density_mean_allowed(client):
    body = zonal(client, "density", operations=["mean"]).json()
    assert body["mean"] == pytest.approx(5)


# Unknown units: sum reported as sum_of_cell_values with a warning.
def test_unknown_units_sum_is_qualified(client):
    body = zonal(client, "unknown-units").json()
    assert body["sum"] == pytest.approx(100)
    assert body["sum_semantics"] == "sum_of_cell_values"
    assert any("must not be interpreted as a population count" in w for w in body["warnings"])


# /health is ok and /ready reports all checks ok.
def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["checks"] == {"asset_root": "ok", "output_root": "ok", "catalog": "ok", "job_store": "ok"}


# /ready gives 503 when the asset root is missing.
def test_ready_reports_missing_asset_root(make_client, tmp_path):
    client, _, _ = make_client()
    client.app.state.service.settings = type(client.app.state.service.settings)(
        asset_root=tmp_path / "nope", output_root=tmp_path / "out"
    )
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["asset_root"] == "missing"


# OpenAPI has the /api server, every route, error responses and strict request schemas.
def test_openapi_contract(client):
    spec = client.get("/openapi.json").json()
    assert spec["servers"] == [{"url": "/api"}]
    paths = spec["paths"]
    for path, method in [
        ("/operations/clip", "post"),
        ("/operations/zonal-statistics", "post"),
        ("/operations/{job_id}", "get"),
        ("/downloads/{job_id}", "get"),
        ("/downloads/{job_id}/metadata", "get"),
        ("/health", "get"),
        ("/ready", "get"),
    ]:
        assert method in paths[path], path
    clip = paths["/operations/clip"]["post"]
    assert "422" in clip["responses"] and "404" in clip["responses"]
    schemas = spec["components"]["schemas"]
    assert schemas["ClipRequest"]["additionalProperties"] is False
    assert {"dataset_id", "aoi", "aoi_crs"} <= set(schemas["ZonalStatisticsRequest"]["required"])
    assert "not** an OGC" in spec["info"]["description"]


# Requests route with the /api prefix forwarded or stripped.
def test_gateway_prefix_forwarded_or_stripped(client):
    stripped = client.post(ZONAL, json=payload())
    forwarded = client.post("/api" + ZONAL, json=payload())
    assert stripped.status_code == forwarded.status_code == 200
    assert forwarded.json()["sum"] == stripped.json()["sum"] == 100
