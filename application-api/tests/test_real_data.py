"""REAL public inputs through the real pipeline (software evidence; NOT CyberGIS scientific outputs).

* WorldPop Global 1 km unconstrained population, India 2011 (CC BY 4.0), 3508 x 3451 float32, EPSG:4326.
* geoBoundaries gbOpen India ADM1, simplified (CC BY 2.5 IN).

Fetch them with ``application-api/scripts/fetch_real_samples.sh`` (checksum-verified into ./data/real-samples);
the tests are skipped when the files are absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
import rasterio

from conftest import REPO, upload, wait_job

pytestmark = [pytest.mark.db, pytest.mark.real_data]

SAMPLES = Path(os.environ.get("APP_REAL_SAMPLES", REPO / "data" / "real-samples"))
WORLDPOP = SAMPLES / "ind_ppp_2011_1km_Aggregated.tif"
ADM1 = SAMPLES / "geoBoundaries-IND-ADM1_simplified.geojson"
WORLDPOP_SHA = "3a058a6e5cd42c9d18cf8c77e0eeb5f09dbc84a3053e963b981147fb67769703"
ADM1_SHA = "4c63fe43294a391e8f2de4e9f86f3edb60f8688275b9fee90c61fb2aa0c26061"
HYDERABAD = {"type": "Polygon", "coordinates": [[[78.30, 17.25], [78.65, 17.25], [78.65, 17.55], [78.30, 17.55],
                                                 [78.30, 17.25]]]}
EVIDENCE = []


def _need(path: Path, sha: str) -> None:
    if not path.is_file():
        pytest.skip(f"{path.name} not present; run scripts/fetch_real_samples.sh")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, f"{path.name} does not match the pinned checksum"


def _timed(label, fn):
    t = time.monotonic()
    out = fn()
    EVIDENCE.append({"step": label, "seconds": round(time.monotonic() - t, 3)})
    return out


@pytest.fixture(scope="module", autouse=True)
def _write_evidence():
    yield
    out = os.environ.get("APP_EVIDENCE_OUT")
    if out and EVIDENCE:
        Path(out).write_text(json.dumps(EVIDENCE, indent=2, default=str))


def test_real_worldpop_upload_publish_export(admin_client, user_client, run_ingest, run_export, roots, db):
    _need(WORLDPOP, WORLDPOP_SHA)
    meta = {"dataset_id": "real-worldpop-1km-ind-2011", "version": 1, "kind": "raster", "product_level": "source",
            "title": "WorldPop unconstrained population count 2011, India, 1 km (real sample)", "year": 2011,
            "units": "people_per_cell", "checksum_sha256": WORLDPOP_SHA,
            "source": "WorldPop Global 2000-2020 1km unconstrained, IND 2011 (ind_ppp_2011_1km_Aggregated.tif)",
            "provenance": {"license": "CC BY 4.0", "producer": "WorldPop, University of Southampton",
                           "synthetic": False, "notes": "software test input; not a CyberGIS product"},
            "expect": {"crs": "EPSG:4326", "width": 3508, "height": 3451, "nodata": -99999.0}}
    r = _timed("upload 19 MB GeoTIFF", lambda: upload(admin_client, WORLDPOP, meta))
    assert r.status_code == 202, r.text
    job = _timed("validate (importer, full pixel scan)", lambda: wait_job(admin_client, r.json()["job_id"], run_ingest))
    assert job["status"] == "completed", job
    summary = job["result"]["validation"]["summary"]
    EVIDENCE.append({"step": "validation summary", "summary": summary})
    assert summary["crs"] == "EPSG:4326" and summary["width"] == 3508 and summary["height"] == 3451
    assert 1.0e9 < summary["sum"] < 1.4e9  # India 2011: ~1.2 billion people in WorldPop's unconstrained counts

    r = admin_client.post("/admin/datasets/real-worldpop-1km-ind-2011/versions/1/publish")
    job = _timed("publish (COG conversion + catalog)", lambda: wait_job(admin_client, r.json()["job_id"], run_ingest))
    assert job["status"] == "completed" and job["result"]["outcome"] == "imported", job

    cat = user_client.get("/api/catalog/datasets/real-worldpop-1km-ind-2011").json()
    assert cat["crs"] == "EPSG:4326" and cat["nodata"] == -99999.0 and cat["width"] == 3508
    assert cat["license"] == "CC BY 4.0" and cat["synthetic"] is False
    assert "(link withheld)" not in (cat["source"] or "")

    native = user_client.post("/api/exports", json={"dataset_id": "real-worldpop-1km-ind-2011", "kind": "full",
                                                    "format": "COG"}).json()
    data = _timed("native COG download", lambda: user_client.get(native["links"]["download"]).content)
    assert hashlib.sha256(data).hexdigest() == native["result"]["sha256"]
    with rasterio.MemoryFile(data) as mem, mem.open() as cog, rasterio.open(WORLDPOP) as src:
        assert cog.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert (cog.width, cog.height, cog.crs, cog.nodata) == (src.width, src.height, src.crs, src.nodata)
        assert cog.transform.almost_equals(src.transform)
        win = rasterio.windows.Window(1500, 1500, 300, 300)
        assert np.array_equal(cog.read(1, window=win), src.read(1, window=win))  # lossless

    r = user_client.post("/api/exports", json={"dataset_id": "real-worldpop-1km-ind-2011", "kind": "clip",
                                               "aoi": HYDERABAD, "aoi_crs": "EPSG:4326", "format": "GTiff"})
    assert r.status_code == 202, r.text
    _timed("clip Hyderabad AOI", run_export)
    clip = user_client.get(f"/api/exports/{r.json()['job_id']}").json()
    assert clip["status"] == "completed", clip
    out = clip["result"]["output"]
    EVIDENCE.append({"step": "clip result", "output": out})
    with rasterio.MemoryFile(user_client.get(clip["links"]["download"]).content) as mem, mem.open() as c, \
            rasterio.open(WORLDPOP) as src:
        w = rasterio.windows.from_bounds(*c.bounds, transform=src.transform).round_offsets().round_lengths()
        expected = src.read(1, window=w)
        got = c.read(1)
        valid = got != c.nodata
        assert valid.sum() == out["count_valid"] > 0
        assert np.array_equal(got[valid], expected[valid])
        assert abs(float(got[valid].astype(np.float64).sum()) - out["sum_valid"]) < 1e-3 * max(1.0, out["sum_valid"])

    r = user_client.post("/api/exports", json={"dataset_id": "real-worldpop-1km-ind-2011", "kind": "full",
                                               "format": "COG", "output_crs": "EPSG:32644"})
    assert r.status_code == 202, r.text
    _timed("national reprojection to EPSG:32644 COG (strip-wise)", run_export)
    rep = user_client.get(f"/api/exports/{r.json()['job_id']}").json()
    assert rep["status"] == "completed", rep
    EVIDENCE.append({"step": "reprojection result", "output": rep["result"]["output"],
                     "size_bytes": rep["result"]["size_bytes"]})
    with rasterio.MemoryFile(user_client.get(rep["links"]["download"]).content) as mem, mem.open() as ds:
        assert ds.crs.to_epsg() == 32644 and ds.nodata == -99999.0
        assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"


def test_real_geoboundaries_adm1(admin_client, user_client, run_ingest, run_export):
    _need(ADM1, ADM1_SHA)
    meta = {"dataset_id": "real-geoboundaries-ind-adm1", "version": 1, "kind": "vector", "product_level": "source",
            "title": "geoBoundaries India ADM1 (states), simplified (real sample)", "year": 2011,
            "source": "geoBoundaries gbOpen IND ADM1 release 9469f09 (DataMeet India, Election Commission of India)",
            "provenance": {"license": "CC BY 2.5 IN", "synthetic": False},
            "id_property": "shapeID", "name_property": "shapeName", "level_property": "shapeType",
            "unit_id_prefix": "gb-ind-adm1:"}
    r = upload(admin_client, ADM1, meta)
    assert r.status_code == 202, r.text
    job = _timed("validate geoBoundaries ADM1", lambda: wait_job(admin_client, r.json()["job_id"], run_ingest))
    EVIDENCE.append({"step": "geoBoundaries validation", "status": job["status"], "error": job.get("error"),
                     "summary": (job.get("result") or {}).get("validation", {}).get("summary")})
    if job["status"] == "failed":
        # The importer refuses invalid geometries instead of repairing them; that is the contract.
        assert job["error"]["code"] in ("INVALID_GEOMETRY",), job
        pytest.xfail(f"real geoBoundaries file rejected by the importer: {job['error']['message'][:200]}")
    r = admin_client.post("/admin/datasets/real-geoboundaries-ind-adm1/versions/1/publish")
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "completed", job
    r = user_client.post("/api/exports", json={"dataset_id": "real-geoboundaries-ind-adm1", "kind": "clip",
                                               "aoi": HYDERABAD, "format": "GeoJSON"})
    run_export()
    clip = user_client.get(f"/api/exports/{r.json()['job_id']}").json()
    assert clip["status"] == "completed", clip
    fc = json.loads(user_client.get(clip["links"]["download"]).content)
    names = sorted(f["properties"]["name"] for f in fc["features"])
    EVIDENCE.append({"step": "ADM1 clip around Hyderabad", "features": names})
    import unicodedata

    plain = {unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode() for n in names}
    assert "Telangana" in plain  # geoBoundaries spells it "Telangāna"
