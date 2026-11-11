# PostGIS integration of the catalog.current_datasets and ops.jobs backends. Skipped unless
# TEST_DATABASE_URL points at a writable PostGIS database (`pytest -m postgis`); uses the real
# migrations if applied, else creates a minimal schema. Test rows use unique IDs and are deleted.

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
from spatial_processing.backends import COMPLETED, FAILED, QUEUED, RUNNING, PostgresCatalog, PostgresJobStore  # noqa: E402
from spatial_processing.config import ApiError, Settings  # noqa: E402
from synthetic import CLIP, DISJOINT, TOWN_A, TOWN_B, ZONAL, payload  # noqa: E402

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


# Ensure the schema exists, insert the test catalog rows and delete them (and their jobs) afterwards.
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
                # The real schema requires a 64-hex checksum and, for ready rasters, resolution_x/y.
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


# The catalog returns the highest ready version with its provenance.
def test_catalog_resolves_highest_ready_version(db):
    catalog = PostgresCatalog(DSN)
    catalog.ping()
    record = catalog.get_ready(f"{PREFIX}-ver")
    assert record.version == 2 and record.asset_path == "nested/v2.tif"
    assert record.provenance == {"synthetic": True}


# Unknown, staged-only and injection-style dataset IDs give the right errors.
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


# An unreachable database gives 503.
def test_catalog_unavailable():
    with pytest.raises(ApiError) as info:
        PostgresCatalog("postgresql://nobody@127.0.0.1:1/none").get_ready("anything")
    assert info.value.status == 503


# ops.jobs create/transition/get round-trip.
def test_job_store_roundtrip(db):
    store = PostgresJobStore(DSN)
    store.ping()
    job = store.create("clip", {"dataset_id": f"{PREFIX}-2001"})
    assert store.get(job.job_id).status == "queued"
    store.transition(job.job_id, RUNNING)
    done = store.transition(job.job_id, COMPLETED, result_path=f"{job.job_id}/x.tif")
    assert done.status == "completed" and done.result_path == f"{job.job_id}/x.tif"
    assert done.updated_at >= done.created_at


# The full API (ready, zonal, clip, download, failed job) works on the PostGIS backends.
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
            body = client.post(ZONAL, json=payload(f"{PREFIX}-{suffix}", aoi)).json()
            assert body["sum"] == pytest.approx(expected)
        job = client.post(CLIP, json=payload(f"{PREFIX}-2011", TOWN_B)).json()
        assert client.get(f"/operations/{job['job_id']}").json()["status"] == "completed"
        assert client.get(f"/downloads/{job['job_id']}").status_code == 200
        bad = client.post(ZONAL, json=payload(f"{PREFIX}-2001", DISJOINT))
        failed = client.get(f"/operations/{bad.json()['error']['details']['job_id']}").json()
        assert failed["status"] == "failed" and failed["error"]["code"] == "AOI_OUTSIDE_RASTER"


# ---------------------------------------------------------------------------- async queue (core/0009)
# A PostgresJobStore with the async columns (skips otherwise); deletes the jobs it created.
@pytest.fixture()
def async_store():
    store = PostgresJobStore(DSN)
    if not store.supports_async:
        pytest.skip("ops.jobs lacks the async columns of database migration core/0009")
    created: list[uuid.UUID] = []
    yield store, created
    with psycopg.connect(DSN, autocommit=True) as conn:
        for job_id in created:
            conn.execute("DELETE FROM ops.jobs WHERE job_id = %s", (job_id,))


# Claim (no double claims), heartbeat/progress, cancel flag and stale-job recovery on ops.jobs.
def test_postgres_async_claim_heartbeat_cancel_and_recovery(async_store):
    store, created = async_store
    kind = "zonal-statistics"
    a = store.create(kind, {"dataset_id": "x", "t": "a"}, execution="async", max_attempts=2)
    b = store.create(kind, {"dataset_id": "x", "t": "b"}, execution="async", max_attempts=1)
    created += [a.job_id, b.job_id]
    assert a.status == QUEUED and a.execution == "async"

    # two workers never get the same job (FOR UPDATE SKIP LOCKED), oldest first
    claimed = [store.claim("w1", (kind,)), store.claim("w2", (kind,))]
    mine = {j.job_id for j in claimed if j is not None and j.job_id in (a.job_id, b.job_id)}
    assert a.job_id in mine
    assert all(j.attempts == 1 and j.status == RUNNING for j in claimed if j is not None and j.job_id in mine)

    assert store.heartbeat(a.job_id, 0.5, "half") is False
    assert store.get(a.job_id).progress == 0.5
    store.request_cancel(a.job_id)
    assert store.heartbeat(a.job_id, 0.6, None) is True, "worker learns about the cancellation"

    # both stop heart-beating: a (cancel requested) fails; b (1 attempt) fails with WORKER_LOST
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE ops.jobs SET heartbeat_at = now() - interval '10 minutes' WHERE job_id = ANY(%s)",
                     ([a.job_id, b.job_id],))
    store.recover_stale(60)
    assert store.get(a.job_id).status == FAILED
    if store.get(b.job_id).status != QUEUED:  # b was claimed above unless another test's job was older
        assert store.get(b.job_id).error["code"] == "WORKER_LOST"


# A stale running job with attempts left is re-queued.
def test_postgres_stale_job_is_requeued_for_retry(async_store):
    store, created = async_store
    job = store.create("clip", {"dataset_id": "y"}, execution="async", max_attempts=3)
    created.append(job.job_id)
    with psycopg.connect(DSN, autocommit=True) as conn:  # isolate: claim exactly this job
        conn.execute("UPDATE ops.jobs SET status = 'running', attempts = 1, heartbeat_at = now() - interval '1 hour'"
                     " WHERE job_id = %s", (job.job_id,))
    out = store.recover_stale(60)
    assert out["requeued"] >= 1 and store.get(job.job_id).status == QUEUED
    assert store.get(job.job_id).attempts == 1
