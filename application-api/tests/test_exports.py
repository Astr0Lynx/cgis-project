# Exports: native streaming (Range, checksum, constant memory), reprojection and clip jobs (CRS, bounds,
# NoData, values), vector GeoJSON, version selection, guardrails, expiry, cancel and crash recovery.

from __future__ import annotations

import base64
import hashlib
import json
import uuid

import numpy as np
import pytest
import rasterio

import synthetic
from test_admin import publish

pytestmark = pytest.mark.db

AOI = {"type": "Polygon", "coordinates": [[[78.0101, 17.9801], [78.0302, 17.9801], [78.0302, 17.9902],
                                           [78.0101, 17.9902], [78.0101, 17.9801]]]}


# Published synthetic raster dataset for export tests.
@pytest.fixture
def raster_ds(admin_client, run_ingest, samples):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("exp-pop", 1))
    return "exp-pop"


# Published synthetic vector dataset.
@pytest.fixture
def vector_ds(admin_client, run_ingest, samples):
    publish(admin_client, run_ingest, samples["vector"], synthetic.vector_meta("exp-towns", 1, unit_id_prefix="et:"))
    return "exp-towns"


# SHA-256 of bytes.
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# POST /api/exports, assert the status and return the job JSON.
def _export(client, body, expect=(201, 202)):
    r = client.post("/api/exports", json=body)
    assert r.status_code in expect, r.text
    return r.json()


# Run the export worker if needed and return the completed job JSON.
def _finish(client, job, run_export):
    if job["status"] != "completed":
        run_export()
        job = client.get(f"/api/exports/{job['job_id']}").json()
    assert job["status"] == "completed", job
    return job


# Verifies: capabilities are public.
def test_capabilities_are_public(anon_client):
    caps = anon_client.get("/api/exports/capabilities").json()
    assert [f["id"] for f in caps["formats"]["raster"]] == ["GTiff", "COG"]
    assert [f["id"] for f in caps["formats"]["vector"]] == ["GeoJSON"]
    assert caps["kinds"] == ["full", "clip"] and caps["output_crs"]["accepts_any_epsg"] is True


# Verifies: anonymous cannot export.
def test_anonymous_cannot_export(anon_client, raster_ds):
    r = anon_client.post("/api/exports", json={"dataset_id": raster_ds, "kind": "full", "format": "COG"})
    assert r.status_code == 401


# Verifies: native full export streams the published COG.
def test_native_full_export_streams_the_published_cog(user_client, raster_ds, roots):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "COG"}, expect=(201,))
    assert job["status"] == "completed" and job["links"]["download"] == f"/api/exports/{job['job_id']}/download"
    res = job["result"]
    assert res["source"] == "native_asset" and res["format"] == "COG" and res["output"]["resampled"] is False
    assert res["filename"] == "exp-pop_v1.tif" and res["provenance"]["license"] == "CC0-1.0 (test data)"
    asset = (roots["assets"] / "rasters/exp-pop/v1/exp-pop_v1.tif").read_bytes()

    r = user_client.get(job["links"]["download"])
    assert r.status_code == 200 and r.content == asset
    assert r.headers["content-type"].startswith("image/tiff")
    assert r.headers["content-disposition"].startswith("attachment") and "exp-pop_v1.tif" in \
        r.headers["content-disposition"]
    assert r.headers["x-checksum-sha256"] == _sha(asset) == res["sha256"]
    assert r.headers["repr-digest"] == "sha-256=:" + base64.b64encode(bytes.fromhex(_sha(asset))).decode() + ":"
    assert r.headers["content-length"] == str(len(asset)) and r.headers["accept-ranges"] == "bytes"

    part = user_client.get(job["links"]["download"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == asset[:100]
    head = user_client.head(job["links"]["download"])
    assert head.status_code == 200 and head.content == b"" and head.headers["content-length"] == str(len(asset))
    meta = user_client.get(job["links"]["metadata"]).json()
    assert meta["result"]["output"]["crs"] == "EPSG:4326" and meta["result"]["output"]["nodata"] == -9999.0
    assert "rasters/" not in json.dumps(meta)

    gt = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "gtiff"}, expect=(201,))
    assert gt["result"]["format"] == "COG" and gt["result"]["requested_format"] == "GTiff" and gt["result"]["warnings"]
    same_crs = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "COG",
                                     "output_crs": "EPSG:4326"}, expect=(201,))
    assert same_crs["result"]["source"] == "native_asset"


# Verifies: A ~48 MB incompressible raster is streamed in chunks: peak Python allocation stays far below its size.
def test_native_download_memory_is_bounded(admin_client, user_client, run_ingest, tmp_path):
    import tracemalloc

    rng = np.random.default_rng(42)
    path = tmp_path / "big.tif"
    with rasterio.open(path, "w", driver="GTiff", width=3500, height=3500, count=1, dtype="float32", crs="EPSG:4326",
                       transform=rasterio.transform.from_origin(70, 30, 0.001, 0.001), nodata=-1.0, tiled=True) as dst:
        for row in range(0, 3500, 500):
            dst.write(rng.random((500, 3500), dtype=np.float32), 1, window=((row, row + 500), (0, 3500)))
    publish(admin_client, run_ingest, path, synthetic.raster_meta("big-pop", 1))
    job = _export(user_client, {"dataset_id": "big-pop", "kind": "full", "format": "COG"}, expect=(201,))
    size = job["result"]["size_bytes"]
    assert size > 40_000_000
    tracemalloc.start()
    total = 0
    h = hashlib.sha256()
    with user_client.stream("GET", job["links"]["download"]) as r:
        assert r.status_code == 200
        for chunk in r.iter_bytes():
            total += len(chunk)
            h.update(chunk)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert total == size and h.hexdigest() == job["result"]["sha256"]
    assert peak < size / 4, f"peak {peak} bytes for a {size} byte file"


# Verifies: reprojected full export GTiff and COG.
def test_reprojected_full_export_gtiff_and_cog(user_client, raster_ds, run_export, samples):
    with rasterio.open(samples["raster"]) as src:
        source_values = set(np.unique(src.read(1)).tolist())
    for fmt in ("GTiff", "COG"):
        job = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": fmt,
                                    "output_crs": "EPSG:32644"}, expect=(202,))
        assert job["status"] == "queued" and job["links"]["cancel"].endswith("/cancel")
        job = _finish(user_client, job, run_export)
        res = job["result"]
        assert res["output"]["crs"] == "EPSG:32644" and res["output"]["resampled"] is True
        assert any("NOT preserved" in w for w in res["warnings"])
        data = user_client.get(job["links"]["download"]).content
        assert _sha(data) == res["sha256"] and len(data) == res["size_bytes"]
        out = res["filename"]
        assert out.startswith("exp-pop_v1_full_epsg32644_") and out.endswith(".tif")
        with rasterio.MemoryFile(data) as mem, mem.open() as ds:
            assert ds.crs.to_epsg() == 32644 and ds.nodata == -9999.0 and ds.dtypes[0] == "float32"
            arr = ds.read(1)
            assert set(np.unique(arr).tolist()) <= source_values  # nearest neighbour copies values
            assert ds.tags()["CGIS_DATASET_ID"] == "exp-pop" and ds.units[0] == "people_per_cell"
            if fmt == "COG":
                assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
            lon_min, lat_min, lon_max, lat_max = rasterio.warp.transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
            assert abs(lon_min - 78.0) < 0.002 and abs(lat_max - 18.0) < 0.002


# Verifies: clip native values bounds and NoData.
def test_clip_native_values_bounds_and_nodata(user_client, raster_ds, run_export, samples):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "clip", "aoi": AOI, "aoi_crs": "EPSG:4326",
                                "format": "GTiff"}, expect=(202,))
    job = _finish(user_client, job, run_export)
    out = job["result"]["output"]
    assert out["crs"] == "EPSG:4326" and out["resampled"] is False and out["pixels_in_aoi"] > 0
    data = user_client.get(job["links"]["download"]).content
    with rasterio.open(samples["raster"]) as src, rasterio.MemoryFile(data) as mem, mem.open() as clip:
        assert clip.nodata == src.nodata and clip.res == src.res
        left, bottom, right, top = clip.bounds
        assert 78.0101 - 0.0005 <= left and right <= 78.0302 + 0.0005
        assert 17.9801 - 0.0005 <= bottom and top <= 17.9902 + 0.0005
        window = rasterio.windows.from_bounds(*clip.bounds, transform=src.transform).round_offsets().round_lengths()
        expected = src.read(1, window=window)
        got = clip.read(1)
        valid = got != clip.nodata
        assert valid.sum() == out["count_valid"] and np.array_equal(got[valid], expected[valid])


# Verifies: clip with output CRS and COG.
def test_clip_with_output_crs_and_cog(user_client, raster_ds, run_export):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "clip", "aoi": AOI, "format": "COG",
                                "output_crs": "EPSG:3857"}, expect=(202,))
    job = _finish(user_client, job, run_export)
    assert job["result"]["output"]["crs"] == "EPSG:3857" and job["result"]["output"]["resampled"] is True
    with rasterio.MemoryFile(user_client.get(job["links"]["download"]).content) as mem, mem.open() as ds:
        assert ds.crs.to_epsg() == 3857


# Verifies: export validation errors.
@pytest.mark.parametrize("body,status,code", [
    ({"kind": "full", "format": "PNG"}, 422, "UNSUPPORTED_FORMAT"),
    ({"kind": "full", "format": "GeoJSON"}, 422, "UNSUPPORTED_FORMAT"),
    ({"kind": "full", "format": "COG", "output_crs": "EPSG:999999"}, 422, "UNSUPPORTED_CRS"),
    ({"kind": "full", "format": "COG", "output_crs": "+proj=longlat"}, 422, "UNSUPPORTED_CRS"),
    ({"kind": "full", "format": "COG", "output_crs": "EPSG:4979"}, 422, "UNSUPPORTED_CRS"),  # 3D
    ({"kind": "clip", "format": "GTiff"}, 422, "AOI_REQUIRED"),
    ({"kind": "full", "format": "GTiff", "aoi": AOI}, 422, "AOI_NOT_ALLOWED"),
    ({"kind": "clip", "format": "GTiff", "aoi": {"type": "Point", "coordinates": [78, 18]}}, 422,
     "UNSUPPORTED_GEOMETRY_TYPE"),
    ({"kind": "clip", "format": "GTiff", "aoi": {"type": "Polygon", "coordinates": [[[78, 17], [79, 18], [79, 17],
                                                                                     [78, 18], [78, 17]]]}},
     422, "INVALID_GEOMETRY"),
    ({"kind": "clip", "format": "GTiff", "aoi": {"type": "Polygon", "coordinates": [[[10, 10], [11, 10], [11, 11],
                                                                                     [10, 11], [10, 10]]]}},
     422, "AOI_OUTSIDE_DATASET"),
    ({"kind": "clip", "format": "GTiff", "aoi": AOI, "aoi_crs": "EPSG:32644"}, 422, "AOI_OUTSIDE_DATASET"),
    ({"kind": "full", "format": "COG", "version": 7}, 404, "VERSION_NOT_FOUND"),
    ({"kind": "everything", "format": "COG"}, 422, "INVALID_REQUEST"),
])
def test_export_validation_errors(user_client, raster_ds, body, status, code):
    r = user_client.post("/api/exports", json={"dataset_id": raster_ds, **body})
    assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text


# Verifies: unknown or path like dataset.
def test_unknown_or_path_like_dataset(user_client):
    assert user_client.post("/api/exports", json={"dataset_id": "nope", "kind": "full", "format": "COG"}
                            ).json()["error"]["code"] == "DATASET_NOT_FOUND"
    for bad in ("../../etc/passwd", "/data/assets/x.tif", "http://evil/x.tif"):
        r = user_client.post("/api/exports", json={"dataset_id": bad, "kind": "full", "format": "COG"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_DATASET_ID"


# Verifies: huge AOI refused.
def test_huge_aoi_refused(admin_client, user_client, run_ingest, tmp_path):
    path = tmp_path / "wide.tif"
    with rasterio.open(path, "w", driver="GTiff", width=2000, height=2000, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=rasterio.transform.from_origin(70, 30, 0.005, 0.005), nodata=0, tiled=True,
                       compress="deflate") as dst:
        dst.write(np.ones((2000, 2000), dtype="uint8"), 1)
    publish(admin_client, run_ingest, path, synthetic.raster_meta("wide-pop", 1))
    aoi = {"type": "Polygon", "coordinates": [[[70, 20], [80, 20], [80, 30], [70, 30], [70, 20]]]}
    r = user_client.post("/api/exports", json={"dataset_id": "wide-pop", "kind": "clip", "aoi": aoi, "format": "GTiff"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "AOI_TOO_LARGE"


# Verifies: vector exports.
def test_vector_exports(user_client, vector_ds, run_export, roots):
    job = _export(user_client, {"dataset_id": vector_ds, "kind": "full", "format": "GeoJSON"}, expect=(201,))
    fc = json.loads(user_client.get(job["links"]["download"]).content)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2 and "crs" not in fc
    assert job["result"]["media_type"] == "application/geo+json"

    aoi = {"type": "Polygon", "coordinates": [[[78.005, 17.975], [78.015, 17.975], [78.015, 17.985], [78.005, 17.985],
                                               [78.005, 17.975]]]}
    job = _finish(user_client, _export(user_client, {"dataset_id": vector_ds, "kind": "clip", "aoi": aoi,
                                                     "format": "geojson"}, expect=(202,)), run_export)
    fc = json.loads(user_client.get(job["links"]["download"]).content)
    assert [f["id"] for f in fc["features"]] == ["et:town-a"] and job["result"]["output"]["features_clipped"] == 1
    from shapely.geometry import shape

    assert shape(fc["features"][0]["geometry"]).within(shape(aoi).buffer(1e-9))

    job = _finish(user_client, _export(user_client, {"dataset_id": vector_ds, "kind": "full", "format": "GeoJSON",
                                                     "output_crs": "EPSG:32644"}, expect=(202,)), run_export)
    fc = json.loads(user_client.get(job["links"]["download"]).content)
    assert fc["crs"]["properties"]["name"] == "urn:ogc:def:crs:EPSG::32644"
    x, y = fc["features"][0]["geometry"]["coordinates"][0][0][0]
    assert 100_000 < x < 900_000 and 1_000_000 < y < 3_000_000  # metres, not degrees

    nowhere = {"type": "Polygon", "coordinates": [[[78.0405, 17.9405], [78.0406, 17.9405], [78.0406, 17.9406],
                                                   [78.0405, 17.9406], [78.0405, 17.9405]]]}
    r = user_client.post("/api/exports", json={"dataset_id": vector_ds, "kind": "clip", "aoi": nowhere,
                                               "format": "GeoJSON"})
    if r.status_code == 202:
        run_export()
        failed = user_client.get(f"/api/exports/{r.json()['job_id']}").json()
        assert failed["status"] == "failed" and failed["error"]["code"] == "NO_FEATURES_SELECTED"
    else:
        assert r.json()["error"]["code"] == "AOI_OUTSIDE_DATASET"


# Verifies: version selection.
def test_version_selection(admin_client, user_client, run_ingest, samples, roots):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("ver-pop", 1))
    publish(admin_client, run_ingest, samples["raster_v2"], synthetic.raster_meta("ver-pop", 2))
    v1 = (roots["assets"] / "rasters/ver-pop/v1/ver-pop_v1.tif").read_bytes()
    v2 = (roots["assets"] / "rasters/ver-pop/v2/ver-pop_v2.tif").read_bytes()
    cur = _export(user_client, {"dataset_id": "ver-pop", "kind": "full", "format": "COG"}, expect=(201,))
    old = _export(user_client, {"dataset_id": "ver-pop", "version": 1, "kind": "full", "format": "COG"}, expect=(201,))
    assert cur["input"]["version"] == 2 and old["input"]["version"] == 1
    assert user_client.get(cur["links"]["download"]).content == v2
    assert user_client.get(old["links"]["download"]).content == v1


# Verifies: jobs are private to their owner.
def test_jobs_are_private_to_their_owner(user_client, admin_client, make_client, raster_ds, db, settings):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "COG"}, expect=(201,))
    from cgis_app import identity
    from cgis_app.db import connect
    from conftest import login

    with connect(db.api_dsn, application_name="t") as conn:
        identity.create_user(conn, settings, "carol-user", "c4r0l-Strong-pass!", "user")
    carol = make_client()
    carol.headers["X-CSRF-Token"] = login(carol, {"username": "carol-user", "password": "c4r0l-Strong-pass!"})
    for path in ("", "/download", "/metadata"):
        assert carol.get(f"/api/exports/{job['job_id']}{path}").status_code == 404
    assert carol.post(f"/api/exports/{job['job_id']}/cancel").status_code == 404
    assert carol.get("/api/exports").json()["total"] == 0
    assert admin_client.get(f"/api/exports/{job['job_id']}").status_code == 200
    assert admin_client.get("/api/exports?all=true").json()["total"] >= 1
    assert user_client.get(f"/api/exports/{uuid.uuid4()}").status_code == 404


# Verifies: cancel and states.
def test_cancel_and_states(user_client, raster_ds, run_export):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "GTiff",
                                "output_crs": "EPSG:3857"}, expect=(202,))
    assert "download" not in job["links"]
    r = user_client.get(f"/api/exports/{job['job_id']}/download")
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_NOT_COMPLETED"
    r = user_client.post(job["links"]["cancel"])
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and "cancel" not in r.json()["links"]
    assert user_client.post(job["links"]["cancel"]).json()["error"]["code"] == "JOB_FINISHED"
    run_export()  # nothing to run
    assert user_client.get(f"/api/exports/{job['job_id']}").json()["status"] == "cancelled"
    assert user_client.get(f"/api/exports/{job['job_id']}/download").json()["error"]["code"] == "JOB_FAILED"


# Verifies: running job can be cancelled.
def test_running_job_can_be_cancelled(user_client, raster_ds, worker_settings, db):
    from cgis_app import jobs
    from cgis_app.db import connect
    from cgis_app.worker import export as export_worker
    from cgis_app.worker.runner import run_one

    job = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "GTiff",
                                "output_crs": "EPSG:3857"}, expect=(202,))

    # Export handler that requests a cancel, then reports progress until Cancelled is raised.
    def cancelling_handler(ctx):
        user_client.post(f"/api/exports/{job['job_id']}/cancel")
        import time

        for _ in range(100):
            time.sleep(0.05)
            ctx.progress(0.5, "working")  # raises Cancelled once the heartbeat sees the flag
        return export_worker.handle(ctx)

    import threading

    with connect(db.worker_dsn, application_name="t") as conn:
        run_one(conn, db.worker_dsn, worker_settings, "export", cancelling_handler, "test-worker", threading.Event())
        row = jobs.get(conn, uuid.UUID(job["job_id"]))
    assert row["status"] == "cancelled"


# Verifies: expired export is gone.
def test_expired_export_is_gone(user_client, raster_ds, run_export, db, roots, worker_settings):
    job = _finish(user_client, _export(user_client, {"dataset_id": raster_ds, "kind": "clip", "aoi": AOI,
                                                     "format": "GTiff"}, expect=(202,)), run_export)
    folder = roots["exports"] / uuid.UUID(job["job_id"]).hex
    assert folder.is_dir()
    with db.owner() as conn:
        conn.execute("UPDATE cgis_app.jobs SET expires_at = now() - interval '1 second' WHERE job_id = %s",
                     (job["job_id"],))
    r = user_client.get(job["links"]["download"])
    assert r.status_code == 410 and r.json()["error"]["code"] == "EXPORT_EXPIRED"
    from cgis_app.db import connect
    from cgis_app.worker import export as export_worker

    with connect(db.worker_dsn, application_name="t") as conn:
        export_worker.maintenance(conn, worker_settings)
    assert not folder.exists()
    assert user_client.get(f"/api/exports/{job['job_id']}").json()["status"] == "expired"


# Verifies: crashed worker job is recovered.
def test_crashed_worker_job_is_recovered(user_client, raster_ds, run_export, db):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "clip", "aoi": AOI, "format": "COG"}, expect=(202,))
    from cgis_app import jobs
    from cgis_app.db import connect

    with connect(db.worker_dsn, application_name="t") as conn:
        claimed = jobs.claim(conn, "export", "dead-worker", lease_seconds=30)
        assert claimed["job_id"] == uuid.UUID(job["job_id"])
        conn.execute("UPDATE cgis_app.jobs SET lease_expires_at = now() - interval '1 second' WHERE job_id = %s",
                     (claimed["job_id"],))  # the worker died; its lease ran out
    done = _finish(user_client, user_client.get(f"/api/exports/{job['job_id']}").json(), run_export)
    assert done["attempts"] == 2


# Verifies: job abandoned after max attempts.
def test_job_abandoned_after_max_attempts(user_client, raster_ds, run_export, db):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "clip", "aoi": AOI, "format": "GTiff"}, expect=(202,))
    with db.owner() as conn:
        conn.execute("UPDATE cgis_app.jobs SET status = 'running', attempts = max_attempts, lease_owner = 'gone', "
                     "lease_expires_at = now() - interval '1 second' WHERE job_id = %s", (job["job_id"],))
    run_export()
    row = user_client.get(f"/api/exports/{job['job_id']}").json()
    assert row["status"] == "failed" and row["error"]["code"] == "JOB_ABANDONED"


# Verifies: active job limit.
def test_active_job_limit(user_client, raster_ds, app):
    limit = app.state.settings.export_max_active_per_user
    for _ in range(limit):
        _export(user_client, {"dataset_id": raster_ds, "kind": "clip", "aoi": AOI, "format": "GTiff"}, expect=(202,))
    r = user_client.post("/api/exports", json={"dataset_id": raster_ds, "kind": "clip", "aoi": AOI, "format": "GTiff"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_JOBS" and r.headers["retry-after"]


# Verifies: dataset restricted after export.
def test_dataset_restricted_after_export(admin_client, user_client, raster_ds):
    job = _export(user_client, {"dataset_id": raster_ds, "kind": "full", "format": "COG"}, expect=(201,))
    admin_client.put(f"/admin/datasets/{raster_ds}/access", json={"access": "admins"})
    assert user_client.get(job["links"]["download"]).status_code == 403
