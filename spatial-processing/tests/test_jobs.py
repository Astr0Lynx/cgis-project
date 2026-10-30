"""Job status transitions, downloads, concurrency and guardrails."""

from __future__ import annotations

import io
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from rasterio.io import MemoryFile

from spatial_processing.jobs import COMPLETED, FAILED, QUEUED, RUNNING, InMemoryJobStore, InvalidTransition
from synthetic import TOWN_A, TOWN_B


def zonal_payload(dataset_id="synthetic-worldpop-2001", aoi=TOWN_A):
    return {"dataset_id": dataset_id, "aoi": aoi, "aoi_crs": "EPSG:4326"}


def test_zonal_job_is_recorded(client_and_store):
    client, store, _ = client_and_store
    stats = client.post("/operations/zonal-statistics", json=zonal_payload()).json()
    job_id = uuid.UUID(stats["job_id"])
    assert store.history[job_id] == [QUEUED, RUNNING, COMPLETED]

    job = client.get(f"/operations/{job_id}").json()
    assert job["status"] == "completed"
    assert job["kind"] == "zonal-statistics"
    assert job["result"]["sum"] == pytest.approx(100)
    assert job["error"] is None
    assert job["links"] == {"self": f"/api/operations/{job_id}"}
    assert job["input"]["dataset_id"] == "synthetic-worldpop-2001"
    assert store.get(job_id).result_path == f"{job_id}/result.json"


def test_clip_job_links_use_public_prefix(client_and_store):
    client, store, _ = client_and_store
    job = client.post("/operations/clip", json=zonal_payload()).json()
    jid = job["job_id"]
    assert job["links"]["download"] == f"/api/downloads/{jid}"
    assert job["result"]["download_url"] == f"/api/downloads/{jid}"
    assert store.history[uuid.UUID(jid)] == [QUEUED, RUNNING, COMPLETED]
    assert client.get(f"/operations/{jid}").json() == job


def test_unknown_and_malformed_job_ids(client):
    r = client.get(f"/operations/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"
    r = client.get(f"/downloads/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"
    for bad in ("not-a-uuid", "..%2F..%2Fetc%2Fpasswd", "1"):
        r = client.get(f"/downloads/{bad}")
        assert r.status_code in (404, 422)
        assert "error" in r.json()


def test_zonal_job_has_no_download(client):
    jid = client.post("/operations/zonal-statistics", json=zonal_payload()).json()["job_id"]
    r = client.get(f"/downloads/{jid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_DOWNLOAD"


def test_failed_job_is_recorded_and_not_downloadable(client_and_store):
    client, store, settings = client_and_store
    r = client.post("/operations/clip", json=zonal_payload("corrupted"))
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "ASSET_UNREADABLE"
    jid = err["details"]["job_id"]
    assert store.history[uuid.UUID(jid)] == [QUEUED, RUNNING, FAILED]

    job = client.get(f"/operations/{jid}").json()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "ASSET_UNREADABLE"
    assert job["result"] is None
    assert "download" not in job["links"]
    d = client.get(f"/downloads/{jid}")
    assert d.status_code == 409 and d.json()["error"]["code"] == "JOB_FAILED"
    assert not (settings.output_root / jid).exists()  # partial outputs removed


def test_processing_failure_after_acceptance_marks_job_failed(client_and_store):
    client, store, _ = client_and_store
    r = client.post("/operations/zonal-statistics", json=zonal_payload(aoi={"type": "Polygon", "coordinates": [[[79, 17], [79.01, 17], [79.01, 17.01], [79, 17]]]}))
    assert r.status_code == 422
    jid = uuid.UUID(r.json()["error"]["details"]["job_id"])
    assert store.get(jid).status == FAILED
    assert store.get(jid).error["code"] == "AOI_OUTSIDE_RASTER"


def test_input_validation_errors_create_no_job(client_and_store):
    client, store, _ = client_and_store
    client.post("/operations/zonal-statistics", json={**zonal_payload(), "aoi_crs": "EPSG:999999"})
    client.post("/operations/zonal-statistics", json=zonal_payload("no-such-dataset"))
    assert store.history == {}


def test_download_of_unfinished_job(client_and_store):
    client, store, _ = client_and_store
    job = store.create("clip", {"dataset_id": "x", "format": "GTiff"})
    r = client.get(f"/downloads/{job.job_id}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_NOT_COMPLETED"
    store.transition(job.job_id, RUNNING)
    assert client.get(f"/downloads/{job.job_id}").json()["error"]["code"] == "JOB_NOT_COMPLETED"
    assert client.get(f"/operations/{job.job_id}").json()["status"] == "running"


def test_download_after_output_removed_is_gone(client_and_store):
    client, _, settings = client_and_store
    job = client.post("/operations/clip", json=zonal_payload()).json()
    (settings.output_root / job["job_id"] / job["result"]["filename"]).unlink()
    r = client.get(f"/downloads/{job['job_id']}")
    assert r.status_code == 410 and r.json()["error"]["code"] == "OUTPUT_EXPIRED"
    # repeated requests give the same deterministic answer
    assert client.get(f"/downloads/{job['job_id']}").status_code == 410


def test_invalid_status_transitions():
    store = InMemoryJobStore()
    job = store.create("clip", {})
    with pytest.raises(InvalidTransition):
        store.transition(job.job_id, COMPLETED)  # must pass through running
    store.transition(job.job_id, RUNNING)
    store.transition(job.job_id, COMPLETED, result_path="x/y.tif")
    with pytest.raises(InvalidTransition):
        store.transition(job.job_id, FAILED)


def test_concurrent_jobs_do_not_overwrite_each_other(make_client):
    client, store, settings = make_client(max_concurrent_jobs=3, queue_wait_seconds=60)
    cases = [
        ("synthetic-worldpop-2001", TOWN_A, 100),
        ("synthetic-worldpop-2011", TOWN_A, 200),
        ("synthetic-worldpop-2001", TOWN_B, 25),
        ("synthetic-worldpop-2011", TOWN_B, 50),
    ] * 4

    def run(case):
        dataset_id, aoi, _ = case
        return client.post("/operations/clip", json=zonal_payload(dataset_id, aoi))

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(run, cases))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses if r.status_code != 200]
    jobs = [r.json() for r in responses]
    assert len({j["job_id"] for j in jobs}) == len(cases)
    assert len({j["result"]["filename"] for j in jobs}) == len(cases)
    for (_, _, expected), job in zip(cases, jobs):
        content = client.get(f"/downloads/{job['job_id']}").content
        with MemoryFile(io.BytesIO(content)).open() as ds:
            assert np.ma.compressed(ds.read(1, masked=True)).sum() == pytest.approx(expected)
    leftovers = [p for p in settings.output_root.rglob(".tmp-*")]
    assert leftovers == []


def test_busy_when_concurrency_limit_reached(make_client):
    client, store, _ = make_client(max_concurrent_jobs=1, queue_wait_seconds=0)
    service = client.app.state.service
    assert service._slots.acquire(timeout=1)
    try:
        r = client.post("/operations/zonal-statistics", json=zonal_payload())
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "BUSY"
        assert r.headers["retry-after"] == "5"
        assert store.history == {}
    finally:
        service._slots.release()
    assert client.post("/operations/zonal-statistics", json=zonal_payload()).status_code == 200


def test_job_time_limit(make_client):
    client, store, _ = make_client(max_job_seconds=0)
    r = client.post("/operations/clip", json=zonal_payload())
    assert r.status_code == 504
    err = r.json()["error"]
    assert err["code"] == "JOB_TIMEOUT"
    assert store.get(uuid.UUID(err["details"]["job_id"])).status == FAILED
