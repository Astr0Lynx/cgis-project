"""PostGIS integration: catalog.current_datasets + ops.jobs backends.

Skipped unless TEST_DATABASE_URL points at a PostGIS database the test may write to, e.g.

    TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/cgis pytest -m postgis

If Agent 2's migrations are already applied they are used as-is; otherwise a minimal
schema matching contracts/SHARED_CONTRACTS.md is created (test-only, IF NOT EXISTS).
Rows created here use unique IDs and are deleted afterwards.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.postgis

DSN = os.environ.get("TEST_DATABASE_URL")
if not DSN:
    pytest.skip("TEST_DATABASE_URL not set; PostGIS integration tests skipped", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient  # noqa: E402

from spatial_processing.app import create_app  # noqa: E402
from spatial_processing.catalog import PostgresCatalog  # noqa: E402
from spatial_processing.config import Settings  # noqa: E402
from spatial_processing.errors import ApiError  # noqa: E402
from spatial_processing.jobs import COMPLETED, RUNNING, PostgresJobStore  # noqa: E402
from synthetic import TOWN_A, TOWN_B  # noqa: E402

FALLBACK_DDL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS catalog.datasets (
    dataset_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    title TEXT NOT NULL,
    description TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('raster', 'vector')),
    product_level TEXT NOT NULL DEFAULT 'other',
    year INTEGER,
    crs TEXT NOT NULL,
    bbox geometry(Polygon, 4326),
    resolution_x NUMERIC,
    resolution_y NUMERIC,
    units TEXT,
    nodata DOUBLE PRECISION,
    asset_path TEXT,
    checksum_sha256 TEXT,
    source TEXT,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('staged', 'ready', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, version)
);
CREATE OR REPLACE VIEW catalog.current_datasets AS
    SELECT DISTINCT ON (dataset_id) * FROM catalog.datasets
    WHERE status = 'ready' ORDER BY dataset_id, version DESC;
CREATE TABLE IF NOT EXISTS ops.jobs (
    job_id UUID PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_path TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

PREFIX = f"pgtest-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('catalog.current_datasets') IS NOT NULL").fetchone()[0]
        if not exists:
            conn.execute(FALLBACK_DDL)
        rows = [
            (f"{PREFIX}-2001", 1, "ready", "synthetic_2001.tif"),
            (f"{PREFIX}-2011", 1, "ready", "synthetic_2011.tif"),
            (f"{PREFIX}-ver", 1, "ready", "synthetic_2011.tif"),
            (f"{PREFIX}-ver", 2, "ready", "nested/v2.tif"),
            (f"{PREFIX}-ver", 3, "staged", "missing.tif"),
            (f"{PREFIX}-staged", 1, "staged", "synthetic_2001.tif"),
        ]
        for dataset_id, version, status, path in rows:
            conn.execute(
                # Integration (Agent 6): Agent 2's schema requires a 64-hex checksum and, for ready
                # rasters, resolution_x/resolution_y (constraints datasets_checksum_format/_ready_complete).
                "INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, year, crs, bbox, "
                "resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance, status) "
                "VALUES (%s, %s, %s, 'raster', 'source', 2001, 'EPSG:4326', "
                "ST_MakeEnvelope(78.0, 17.936, 78.064, 18.0, 4326), 0.001, 0.001, 'people_per_cell', -9999, %s, %s, "
                "'deterministic-fixture', '{\"synthetic\": true}', %s)",
                (dataset_id, version, f"SYNTHETIC {dataset_id}", path, uuid.uuid4().hex * 2, status),
            )
        yield conn
        conn.execute("DELETE FROM catalog.datasets WHERE dataset_id LIKE %s", (f"{PREFIX}%",))
        conn.execute("DELETE FROM ops.jobs WHERE input->>'dataset_id' LIKE %s", (f"{PREFIX}%",))


def test_catalog_resolves_highest_ready_version(db):
    catalog = PostgresCatalog(DSN)
    catalog.ping()
    record = catalog.get_ready(f"{PREFIX}-ver")
    assert record.version == 2 and record.asset_path == "nested/v2.tif"
    assert record.provenance == {"synthetic": True}


def test_catalog_errors(db):
    catalog = PostgresCatalog(DSN)
    with pytest.raises(ApiError) as info:
        catalog.get_ready(f"{PREFIX}-nope")
    assert info.value.code == "DATASET_NOT_FOUND"
    with pytest.raises(ApiError) as info:
        catalog.get_ready(f"{PREFIX}-staged")
    assert info.value.code == "DATASET_NOT_READY"
    with pytest.raises(ApiError) as info:
        catalog.get_ready("x'; DROP TABLE catalog.datasets; --")
    assert info.value.code == "INVALID_DATASET_ID"


def test_catalog_unavailable():
    with pytest.raises(ApiError) as info:
        PostgresCatalog("postgresql://nobody@127.0.0.1:1/none").get_ready("anything")
    assert info.value.status == 503


def test_job_store_roundtrip(db):
    store = PostgresJobStore(DSN)
    store.ping()
    job = store.create("clip", {"dataset_id": f"{PREFIX}-2001"})
    assert store.get(job.job_id).status == "queued"
    store.transition(job.job_id, RUNNING)
    done = store.transition(job.job_id, COMPLETED, result_path=f"{job.job_id}/x.tif")
    assert done.status == "completed" and done.result_path == f"{job.job_id}/x.tif"
    assert done.updated_at >= done.created_at


def test_api_with_postgis_backends(db, asset_root, tmp_path):
    settings = Settings(
        asset_root=asset_root,
        output_root=tmp_path / "out",
        catalog_backend="postgres",
        job_store_backend="postgres",
        database_url=DSN,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/ready").status_code == 200
        for suffix, aoi, expected in (("2001", TOWN_A, 100), ("2011", TOWN_A, 200), ("2001", TOWN_B, 25), ("2011", TOWN_B, 50)):
            body = client.post(
                "/operations/zonal-statistics",
                json={"dataset_id": f"{PREFIX}-{suffix}", "aoi": aoi, "aoi_crs": "EPSG:4326"},
            ).json()
            assert body["sum"] == pytest.approx(expected)
        job = client.post(
            "/operations/clip", json={"dataset_id": f"{PREFIX}-2011", "aoi": TOWN_B, "aoi_crs": "EPSG:4326"}
        ).json()
        assert client.get(f"/operations/{job['job_id']}").json()["status"] == "completed"
        assert client.get(f"/downloads/{job['job_id']}").status_code == 200
        bad = client.post(
            "/operations/zonal-statistics",
            json={"dataset_id": f"{PREFIX}-2001", "aoi": {"type": "Polygon", "coordinates": [[[79, 17], [79.01, 17], [79.01, 17.01], [79, 17]]]}, "aoi_crs": "EPSG:4326"},
        )
        failed = client.get(f"/operations/{bad.json()['error']['details']['job_id']}").json()
        assert failed["status"] == "failed" and failed["error"]["code"] == "AOI_OUTSIDE_RASTER"
