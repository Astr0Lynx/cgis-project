"""Completion P2/P3: bounded-memory blockwise processing and asynchronous background jobs."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace

import numpy as np
import pytest
import rasterio
from pyproj import CRS

import synthetic
from spatial_processing import blockwise, raster
from spatial_processing.jobs import ASYNC, FAILED, QUEUED, RUNNING
from spatial_processing.units import classify_units
from synthetic import NODATA_CORNER, TOWN_A, TOWN_B, rect

ZONAL = "/operations/zonal-statistics"
CLIP = "/operations/clip"
# Off-lattice vertices: no edge passes exactly through a pixel centre. (At an exact tie the two
# implementations may break the tie differently by one boundary pixel; see docs/API.md.)
IRREGULAR = {"type": "Polygon", "coordinates": [[[78.0031371, 17.9412297], [78.0610833, 17.9521419],
                                                 [78.0401171, 17.9960613], [78.0219887, 17.9703129],
                                                 [78.0061113, 17.9931917], [78.0031371, 17.9412297]]]}
WHOLE = rect(77.99, 17.93, 78.07, 18.01)


def _wait(client, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/operations/{job_id}").json()
        if body["status"] in ("completed", "failed") or time.monotonic() > deadline:
            return body
        time.sleep(0.05)


# ------------------------------------------------------------------ blockwise == in-memory
@pytest.mark.parametrize("aoi", [TOWN_A, TOWN_B, NODATA_CORNER, IRREGULAR, WHOLE],
                         ids=["townA", "townB", "nodata-corner", "irregular", "whole"])
@pytest.mark.parametrize("block", [5, 16, 64])
def test_blockwise_zonal_equals_in_memory(asset_root, aoi, block):
    from shapely.geometry import shape

    geom = shape(aoi)
    sem = classify_units("people_per_cell")
    with rasterio.open(asset_root / "fine_2001.tif") as src:
        ref = raster.zonal_statistics(raster.select_pixels(src, geom, max_window_pixels=10**9,
                                                           catalog_nodata=synthetic.NODATA),
                                      sem, ["sum", "mean", "count"])
        got = blockwise.zonal_blockwise(src, geom, semantics=sem, operations=["sum", "mean", "count"],
                                        catalog_nodata=synthetic.NODATA, block=block)
    for key in ("count_valid", "pixels_in_aoi", "nodata_excluded"):
        assert got[key] == ref[key]
    assert got["sum"] == pytest.approx(ref["sum"], rel=1e-12)
    assert got["mean"] == pytest.approx(ref["mean"], rel=1e-12)
    assert got["processing"]["path"] == "blockwise"


def test_blockwise_skips_blocks_outside_the_aoi(asset_root):
    from shapely.geometry import shape

    l_shape = shape({"type": "Polygon", "coordinates": [[[78.0, 17.936], [78.064, 17.936], [78.064, 17.940],
                                                         [78.004, 17.940], [78.004, 18.0], [78.0, 18.0],
                                                         [78.0, 17.936]]]})
    with rasterio.open(asset_root / "synthetic_2001.tif") as src:
        got = blockwise.zonal_blockwise(src, l_shape, semantics=classify_units("people_per_cell"),
                                        operations=["count"], catalog_nodata=synthetic.NODATA, block=8)
    assert got["processing"]["blocks_read"] < (64 // 8) ** 2


def test_blockwise_integer_raster_sum_is_exact(asset_root):
    from shapely.geometry import shape

    with rasterio.open(asset_root / "unmasked_int.tif") as src:
        ref = raster.zonal_statistics(raster.select_pixels(src, shape(WHOLE), max_window_pixels=10**9),
                                      classify_units("count"), ["sum", "count"])
        got = blockwise.zonal_blockwise(src, shape(WHOLE), semantics=classify_units("count"),
                                        operations=["sum", "count"], catalog_nodata=None, block=7)
    assert isinstance(got["sum"], int) and got["sum"] == ref["sum"] and got["count_valid"] == ref["count_valid"]


@pytest.mark.parametrize("fmt", ["GTiff", "COG"])
@pytest.mark.parametrize("aoi", [TOWN_A, IRREGULAR], ids=["townA", "irregular"])
def test_blockwise_clip_equals_in_memory(asset_root, tmp_path, fmt, aoi):
    from shapely.geometry import shape

    geom = shape(aoi)
    with rasterio.open(asset_root / "fine_2001.tif") as src:
        sel = raster.select_pixels(src, geom, max_window_pixels=10**9, catalog_nodata=synthetic.NODATA)
        ref = raster.write_clip(src, sel, tmp_path / "ref.tif", fmt=fmt, output_crs=None, tags={"A": "1"},
                                units="people_per_cell")
        got = blockwise.clip_blockwise(src, geom, tmp_path / "blk.tif", fmt=fmt, output_crs=None,
                                       tags={"A": "1"}, units="people_per_cell", catalog_nodata=synthetic.NODATA,
                                       block=9, max_output_bytes=10**9)
    out = got["output"]
    for key in ("width", "height", "crs", "transform_gdal", "nodata", "dtype", "count_valid"):
        assert out[key] == ref[key], key
    assert out["sum_valid"] == pytest.approx(ref["sum_valid"], rel=1e-12)
    with rasterio.open(tmp_path / "ref.tif") as a, rasterio.open(tmp_path / "blk.tif") as b:
        assert np.array_equal(a.read(1), b.read(1))
        assert b.tags()["A"] == "1" and b.units[0] == "people_per_cell"
    assert got["count_valid"] == int(sel.valid.sum()) and got["pixels_in_aoi"] == int(sel.inside.sum())


def test_blockwise_clip_reprojects_and_limits_size(asset_root, tmp_path):
    from shapely.geometry import shape

    with rasterio.open(asset_root / "synthetic_2001.tif") as src:
        got = blockwise.clip_blockwise(src, shape(TOWN_A), tmp_path / "w.tif", fmt="GTiff",
                                       output_crs=CRS.from_epsg(3857), tags={}, units=None,
                                       catalog_nodata=synthetic.NODATA, block=4, max_output_bytes=10**9)
        assert got["output"]["crs"] == "EPSG:3857" and got["output"]["resampled"] is True
        assert any("NOT preserved" in w for w in got["output"]["warnings"])
        with pytest.raises(Exception) as ei:
            blockwise.clip_blockwise(src, shape(WHOLE), tmp_path / "big.tif", fmt="GTiff", output_crs=None,
                                     tags={}, units=None, catalog_nodata=synthetic.NODATA, block=16,
                                     max_output_bytes=100)
        assert getattr(ei.value, "code", None) == "OUTPUT_TOO_LARGE"


# ------------------------------------------------------------------ async API
def _body(dataset_id="fine-2001", aoi=IRREGULAR, **extra):
    return {"dataset_id": dataset_id, "aoi": aoi, "aoi_crs": "EPSG:4326", **extra}


def test_async_zonal_job_completes_with_same_numbers(make_client):
    client, store, _ = make_client()
    sync = client.post(ZONAL, json=_body())
    assert sync.status_code == 200
    r = client.post(ZONAL, json=_body(mode="async"))
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["execution"] == "async" and job["status"] in ("queued", "running", "completed")
    assert r.headers["location"] == f"/api/operations/{job['job_id']}"
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    assert done["progress"] == 1.0 and done["attempts"] == 1
    res, ref = done["result"], sync.json()
    assert (res["count_valid"], res["pixels_in_aoi"]) == (ref["count_valid"], ref["pixels_in_aoi"])
    assert res["sum"] == pytest.approx(ref["sum"], rel=1e-12)
    assert res["processing"]["path"] == "blockwise"


def test_async_clip_job_produces_a_download(make_client):
    client, _, _ = make_client(block_size=7)
    job = client.post(CLIP, json=_body(mode="async", format="COG")).json()
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed" and done["links"]["download"].endswith(job["job_id"])
    dl = client.get(f"/downloads/{job['job_id']}")
    assert dl.status_code == 200 and dl.content[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00")
    assert client.get(f"/downloads/{job['job_id']}/metadata").json()["processing"]["path"] == "blockwise"


def test_auto_mode_routes_by_aoi_size(make_client):
    client, _, _ = make_client(max_window_pixels=200)
    small = client.post(ZONAL, json=_body("synthetic-worldpop-2001", TOWN_B, mode="auto"))
    assert small.status_code == 200 and small.json()["count_valid"] == 25
    big = client.post(ZONAL, json=_body("synthetic-worldpop-2001", WHOLE, mode="auto"))
    assert big.status_code == 202
    assert _wait(client, big.json()["job_id"])["result"]["count_valid"] == 60 * 60


def test_sync_large_aoi_gets_413_with_async_hint(make_client):
    client, _, _ = make_client(max_window_pixels=200)
    r = client.post(ZONAL, json=_body("synthetic-worldpop-2001", WHOLE))
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["code"] == "AOI_TOO_LARGE" and "async" in err["details"]["hint"]


def test_async_limit_rejects_without_creating_a_job(make_client):
    client, store, _ = make_client(max_window_pixels=10, async_max_window_pixels=100)
    r = client.post(ZONAL, json=_body("synthetic-worldpop-2001", WHOLE, mode="async"))
    assert r.status_code == 413 and r.json()["error"]["details"]["async_limit"] == 100
    assert store.history == {}


def test_async_unavailable_when_no_workers(make_client):
    client, _, _ = make_client(async_workers=0)
    r = client.post(ZONAL, json=_body(mode="async"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "ASYNC_UNAVAILABLE"


def test_invalid_mode_is_rejected(client):
    r = client.post(ZONAL, json=_body(mode="later"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_REQUEST"


# ------------------------------------------------------------------ cancel / retry / TTL
def test_cancel_queued_job(make_client):
    client, store, _ = make_client()
    client.app.state.workers.stop()            # nothing claims the job
    job = client.post(ZONAL, json=_body(mode="async")).json()
    assert job["status"] == "queued" and job["links"]["cancel"].endswith(f"{job['job_id']}/cancel")
    r = client.post(f"/operations/{job['job_id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert r.json()["error"]["code"] == "CANCELLED"
    again = client.post(f"/operations/{job['job_id']}/cancel")
    assert again.status_code == 409 and again.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"


def test_cancel_running_job_stops_at_checkpoint(make_client):
    client, store, _ = make_client()
    client.app.state.workers.stop()
    job_id = uuid.UUID(client.post(ZONAL, json=_body(mode="async")).json()["job_id"])
    service = client.app.state.service
    claimed = store.claim("test-worker", ("zonal-statistics",))
    assert claimed.status == RUNNING
    store.request_cancel(job_id)
    service.execute_async(claimed)
    assert store.get(job_id).status == FAILED and store.get(job_id).error["code"] == "CANCELLED"
    assert not service.outputs.job_dir(job_id).exists(), "partial outputs removed"


def test_sync_job_cannot_be_cancelled(client):
    job_id = client.post(ZONAL, json=_body("synthetic-worldpop-2001", TOWN_A)).json()["job_id"]
    r = client.post(f"/operations/{job_id}/cancel")
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_NOT_CANCELLABLE"


def test_stale_running_job_is_retried_then_failed(make_client):
    client, store, settings = make_client(async_max_attempts=2)
    pool = client.app.state.workers
    pool.stop()
    job_id = uuid.UUID(client.post(ZONAL, json=_body(mode="async")).json()["job_id"])
    store.claim("dead-worker", ("zonal-statistics",))            # attempt 1, then the worker "dies"
    store.age(job_id, heartbeat_seconds=settings.worker_stale_seconds + 5)
    assert pool.maintenance(force=True)["requeued"] == 1
    assert store.get(job_id).status == QUEUED
    store.claim("dead-worker-2", ("zonal-statistics",))          # attempt 2 also dies
    store.age(job_id, heartbeat_seconds=settings.worker_stale_seconds + 5)
    assert pool.maintenance(force=True)["failed"] == 1
    assert store.get(job_id).error["code"] == "WORKER_LOST"


def test_requeued_job_completes_on_a_new_worker(make_client):
    client, store, settings = make_client(async_max_attempts=2)
    pool = client.app.state.workers
    pool.stop()
    job_id = uuid.UUID(client.post(ZONAL, json=_body(mode="async")).json()["job_id"])
    store.claim("dead-worker", ("zonal-statistics",))
    store.age(job_id, heartbeat_seconds=settings.worker_stale_seconds + 5)
    pool.maintenance(force=True)
    assert pool.run_once() is True
    job = store.get(job_id)
    assert job.status == "completed" and job.attempts == 2


def test_queued_job_expires_after_ttl(make_client):
    client, store, settings = make_client()
    pool = client.app.state.workers
    pool.stop()
    job_id = uuid.UUID(client.post(ZONAL, json=_body(mode="async")).json()["job_id"])
    store.age(job_id, created_seconds=settings.async_queue_ttl_seconds + 1)
    assert pool.maintenance(force=True)["expired"] == 1
    assert store.get(job_id).error["code"] == "JOB_EXPIRED"


def test_dataset_version_change_while_queued_fails_safely(make_client, catalog):
    client, store, _ = make_client()
    client.app.state.workers.stop()
    job_id = uuid.UUID(client.post(ZONAL, json=_body("versioned", TOWN_A, mode="async")).json()["job_id"])
    catalog.add(replace(catalog.get_ready("versioned"), version=9))
    claimed = store.claim("w", ("zonal-statistics",))
    client.app.state.service.execute_async(claimed)
    assert store.get(job_id).error["code"] == "DATASET_VERSION_CHANGED"


def test_async_time_limit(make_client):
    client, store, _ = make_client(async_max_job_seconds=0)
    job = client.post(ZONAL, json=_body(mode="async")).json()
    done = _wait(client, job["job_id"])
    assert done["status"] == "failed" and done["error"]["code"] == "JOB_TIMEOUT"


def test_job_store_records_async_execution(make_client):
    client, store, _ = make_client()
    client.app.state.workers.stop()
    job_id = uuid.UUID(client.post(CLIP, json=_body(mode="async")).json()["job_id"])
    job = store.get(job_id)
    assert job.execution == ASYNC and job.max_attempts == 2
    assert job.input["candidate_window_pixels"] > 0 and "aoi" in job.input
