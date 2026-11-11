# Bounded-memory benchmark on a large SYNTHETIC raster (APP_BENCH=1, size APP_BENCH_SIZE; skipped otherwise):
# upload -> validate -> publish -> native download -> reprojection, peak RSS per worker process; JSON to APP_EVIDENCE_OUT.

from __future__ import annotations

import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import rasterio

import synthetic
from conftest import APP_ROOT, REPO, upload, wait_job

pytestmark = [pytest.mark.db]
SIZE = int(os.environ.get("APP_BENCH_SIZE", "16000"))


# Run one worker pass in its own process; returns seconds and peak RSS.
def _worker(queue: str, db, roots, py: str, extra: dict | None = None) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
           "PYTHONPATH": os.pathsep.join([str(APP_ROOT / "src"), str(REPO), str(REPO / "spatial-processing/src")]),
           "APP_DATABASE_URL": db.worker_dsn, "APP_STAGING_ROOT": str(roots["staging"]),
           "APP_ASSET_ROOT": str(roots["assets"]), "APP_EXPORT_ROOT": str(roots["exports"]),
           "APP_MIN_FREE_BYTES": str(256 * 2**20), "APP_WORKER_POLL_SECONDS": "0.05", "APP_LOG_LEVEL": "WARNING",
           "APP_EXPORT_MAX_PIXELS": str(2_000_000_000), **(extra or {})}
    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t = time.monotonic()
    proc = subprocess.run(["/usr/bin/time", "-f", "%M", py, "-P", "-m", "cgis_app", "worker", "--queue", queue,
                           "--once"], env=env, capture_output=True, text=True, timeout=3600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    peak_kb = int(proc.stderr.strip().splitlines()[-1])
    return {"queue": queue, "seconds": round(time.monotonic() - t, 2), "peak_rss_mib": round(peak_kb / 1024, 1),
            "ru_maxrss_children_kib_before": before}


# Verifies: large raster bounded memory.
def test_large_raster_bounded_memory(admin_client, user_client, db, roots, tmp_path, settings):
    if os.environ.get("APP_BENCH") != "1":
        pytest.skip("set APP_BENCH=1 to run the large-raster benchmark")
    ingest_py = os.environ.get("APP_TEST_INGEST_PYTHON") or sys.executable
    path = tmp_path / "large.tif"
    t = time.monotonic()
    with rasterio.open(path, "w", driver="GTiff", width=SIZE, height=SIZE, count=1, dtype="float32", crs="EPSG:4326",
                       transform=rasterio.transform.from_origin(70.0, 35.0, 0.0008333333, 0.0008333333),
                       nodata=-99999.0, tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=3,
                       bigtiff="YES") as dst:
        cols = np.arange(SIZE, dtype=np.float32)
        for row in range(0, SIZE, 512):
            h = min(512, SIZE - row)
            block = (np.add.outer(np.arange(row, row + h, dtype=np.float32), cols) % 97).astype(np.float32)
            block[:, : SIZE // 8] = -99999.0  # a NoData "sea" strip
            dst.write(block, 1, window=((row, row + h), (0, SIZE)))
    evidence = {"cells": SIZE * SIZE, "uncompressed_bytes": SIZE * SIZE * 4, "file_bytes": path.stat().st_size,
                "generate_seconds": round(time.monotonic() - t, 1), "steps": []}
    import dataclasses

    app_settings = admin_client.app.state.settings
    admin_client.app.state.settings = dataclasses.replace(app_settings, max_upload_bytes=4 * 2**30,
                                                          export_max_pixels=2_000_000_000)
    user_client.app.state.settings = admin_client.app.state.settings
    t = time.monotonic()
    r = upload(admin_client, path, synthetic.raster_meta("bench-large", 1))
    assert r.status_code == 202, r.text
    evidence["steps"].append({"step": "upload (streamed)", "seconds": round(time.monotonic() - t, 2)})
    evidence["steps"].append({"step": "validate", **_worker("ingest", db, roots, ingest_py)})
    assert admin_client.get(f"/admin/jobs/{r.json()['job_id']}").json()["status"] == "completed"
    r = admin_client.post("/admin/datasets/bench-large/versions/1/publish")
    evidence["steps"].append({"step": "publish (COG)", **_worker("ingest", db, roots, ingest_py)})
    job = admin_client.get(f"/admin/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "completed", job

    native = user_client.post("/api/exports", json={"dataset_id": "bench-large", "kind": "full", "format": "COG"}).json()
    import tracemalloc

    tracemalloc.start()
    t = time.monotonic()
    h = hashlib.sha256()
    n = 0
    with user_client.stream("GET", native["links"]["download"]) as resp:
        for chunk in resp.iter_bytes():
            n += len(chunk)
            h.update(chunk)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert h.hexdigest() == native["result"]["sha256"]
    evidence["steps"].append({"step": "native COG download", "bytes": n, "seconds": round(time.monotonic() - t, 2),
                              "python_peak_alloc_mib": round(peak / 2**20, 1)})

    r = user_client.post("/api/exports", json={"dataset_id": "bench-large", "kind": "full", "format": "COG",
                                               "output_crs": "EPSG:32644"})
    assert r.status_code == 202, r.text
    evidence["steps"].append({"step": "reproject to EPSG:32644 COG", **_worker("export", db, roots, sys.executable)})
    rep = user_client.get(f"/api/exports/{r.json()['job_id']}").json()
    assert rep["status"] == "completed", rep
    evidence["steps"][-1]["output"] = {k: rep["result"]["output"][k] for k in ("width", "height", "crs", "layout")}
    evidence["steps"][-1]["size_bytes"] = rep["result"]["size_bytes"]
    out = os.environ.get("APP_EVIDENCE_OUT")
    if out:
        Path(out).write_text(json.dumps(evidence, indent=2))
    for step in evidence["steps"]:
        if "peak_rss_mib" in step:
            assert step["peak_rss_mib"] < 1536, step  # far below the 1 GiB raster; bounded by blocks/strips
