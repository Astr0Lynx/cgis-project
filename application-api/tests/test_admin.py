"""Admin dataset lifecycle through the real importer: upload -> validate -> publish -> version status, idempotency,
conflicts, negative inputs (bad CRS, corrupt TIFF, invalid geometry, bombs, traversal, oversize) and access policy."""

from __future__ import annotations

import hashlib
import json

import pytest

import synthetic
from conftest import upload, wait_job

pytestmark = pytest.mark.db


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(client, run_ingest, path, meta) -> dict:
    """Upload, validate and publish; returns the publish job."""
    r = upload(client, path, meta)
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"], run_ingest)
    assert job["status"] == "completed", job
    did, ver = job["dataset_id"], job["version"]
    r = client.post(f"/admin/datasets/{did}/versions/{ver}/publish")
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"], run_ingest)
    assert job["status"] == "completed", job
    return job


def test_raster_upload_validate_publish_and_discover(admin_client, user_client, run_ingest, samples, db, roots):
    meta = synthetic.raster_meta("test-pop-2011", 1)
    r = upload(admin_client, samples["raster"], meta)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["kind"] == "upload" and job["status"] == "queued" and job["version"] == 1
    assert job["result"]["checksum_sha256"] == _sha(samples["raster"])
    assert job["links"]["self"] == f"/admin/jobs/{job['job_id']}"
    staged = list(roots["staging"].iterdir())
    assert len(staged) == 1 and staged[0].name != samples["raster"].name and staged[0].suffix == ".tif"

    detail = admin_client.get("/admin/datasets/test-pop-2011").json()
    assert detail["versions"][0]["status"] == "validating"

    job = wait_job(admin_client, job["job_id"], run_ingest)
    assert job["status"] == "completed" and job["result"]["outcome"] == "validated", job
    summary = job["result"]["validation"]["summary"]
    assert summary["crs"] == "EPSG:4326" and summary["width"] == 100 and summary["height"] == 80
    version = admin_client.get("/admin/datasets/test-pop-2011/versions/1").json()
    assert version["status"] == "validated" and version["actions"] == ["publish", "discard"]
    assert version["validation"]["ok"] is True and version["created_by"] == "alice-admin"
    assert version["original_filename"] == samples["raster"].name

    # nothing is visible to users before publication
    assert user_client.get("/api/catalog/datasets/test-pop-2011").status_code == 404

    r = admin_client.post("/admin/datasets/test-pop-2011/versions/1/publish")
    assert r.status_code == 202 and r.json()["kind"] == "publish"
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "completed" and job["result"]["outcome"] == "imported", job
    assert job["result"]["links"]["ogc_collection"] == "/ogc/collections/test-pop-2011"
    assert list(roots["staging"].iterdir()) == []  # staged bytes removed after publication

    with db.owner() as conn:
        row = conn.execute("SELECT status, asset_path, checksum_sha256, crs, units FROM catalog.current_datasets "
                           "WHERE dataset_id = 'test-pop-2011'").fetchone()
    assert row["status"] == "ready" and row["checksum_sha256"] == _sha(samples["raster"])
    assert row["asset_path"] == "rasters/test-pop-2011/v1/test-pop-2011_v1.tif"
    assert (roots["assets"] / row["asset_path"]).is_file()

    pub = user_client.get("/api/catalog/datasets/test-pop-2011")
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body["version"] == 1 and body["crs"] == "EPSG:4326" and body["units"] == "people_per_cell"
    assert body["nodata"] == -9999.0 and body["resolution_x"] == pytest.approx(0.0005) and body["width"] == 100
    assert body["license"] == "CC0-1.0 (test data)" and body["synthetic"] is True and body["group_id"] == "test-group"
    text = json.dumps(body)
    assert "rasters/" not in text and "asset_path" not in text and "alice-admin" not in text
    listing = admin_client.get("/admin/datasets").json()
    assert listing["total"] == 1 and listing["items"][0]["current_version"] == 1
    assert listing["items"][0]["latest_status"] == "ready"


def test_repeat_upload_is_idempotent_and_conflict_needs_new_version(admin_client, run_ingest, samples, db, roots):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("idem-pop", 1))
    asset = roots["assets"] / "rasters/idem-pop/v1/idem-pop_v1.tif"
    before = _sha(asset)

    r = upload(admin_client, samples["raster"], synthetic.raster_meta("idem-pop", 1))
    assert r.status_code == 200 and r.json()["result"]["outcome"] == "unchanged" and r.json()["status"] == "completed"
    assert list(roots["staging"].iterdir()) == []

    r = upload(admin_client, samples["raster_v2"], synthetic.raster_meta("idem-pop", 1))
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "CHECKSUM_CONFLICT" and err["details"]["suggested_version"] == 2
    assert list(roots["staging"].iterdir()) == []
    assert _sha(asset) == before  # the ready original is untouched

    job = publish(admin_client, run_ingest, samples["raster_v2"], synthetic.raster_meta("idem-pop", 2))
    assert job["version"] == 2
    with db.owner() as conn:
        rows = conn.execute("SELECT version, status FROM catalog.datasets WHERE dataset_id = 'idem-pop' "
                            "ORDER BY version").fetchall()
        current = conn.execute("SELECT version FROM catalog.current_datasets WHERE dataset_id='idem-pop'").fetchone()
    assert [(r["version"], r["status"]) for r in rows] == [(1, "ready"), (2, "ready")] and current["version"] == 2
    assert _sha(asset) == before


def test_version_is_assigned_when_omitted(admin_client, run_ingest, samples):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("auto-ver", 1))
    r = upload(admin_client, samples["raster_v2"], synthetic.raster_meta("auto-ver", None))
    assert r.status_code == 202 and r.json()["version"] == 2


def test_staged_version_conflict_and_discard(admin_client, run_ingest, samples, roots):
    r = upload(admin_client, samples["raster"], synthetic.raster_meta("staged-x", 1))
    assert r.status_code == 202
    same = upload(admin_client, samples["raster"], synthetic.raster_meta("staged-x", 1))
    assert same.status_code == 200 and same.json()["result"]["outcome"] == "already_uploaded"
    other = upload(admin_client, samples["raster_v2"], synthetic.raster_meta("staged-x", 1))
    assert other.status_code == 409 and other.json()["error"]["code"] == "VERSION_EXISTS"
    assert admin_client.delete("/admin/datasets/staged-x/versions/1").status_code == 409  # still validating
    wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert admin_client.delete("/admin/datasets/staged-x/versions/1").status_code == 204
    assert list(roots["staging"].iterdir()) == []
    r = upload(admin_client, samples["raster_v2"], synthetic.raster_meta("staged-x", 1))
    assert r.status_code == 202


def test_state_machine_guards(admin_client, run_ingest, samples):
    r = upload(admin_client, samples["raster"], synthetic.raster_meta("guards", 1))
    assert admin_client.post("/admin/datasets/guards/versions/1/publish").status_code == 409  # not validated yet
    wait_job(admin_client, r.json()["job_id"], run_ingest)
    r = admin_client.post("/admin/datasets/guards/versions/1/publish")
    assert r.status_code == 202
    assert admin_client.post("/admin/datasets/guards/versions/1/publish").json()["error"]["code"] == "INVALID_STATE"
    wait_job(admin_client, r.json()["job_id"], run_ingest)
    r = admin_client.post("/admin/datasets/guards/versions/1/validate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "READY_IMMUTABLE"
    assert admin_client.delete("/admin/datasets/guards/versions/1").json()["error"]["code"] == "READY_IMMUTABLE"
    assert admin_client.post("/admin/datasets/guards/versions/9/publish").status_code == 404


@pytest.mark.parametrize("sample,code", [
    ("no_crs", "MISSING_CRS"),
    ("unknown_crs", "UNSUPPORTED_CRS"),
    ("corrupt", "CORRUPT_RASTER"),
    ("truncated", "CORRUPT_RASTER"),
])
def test_bad_rasters_fail_validation_with_codes(admin_client, run_ingest, samples, db, sample, code):
    r = upload(admin_client, samples[sample], synthetic.raster_meta("bad-raster", 1))
    assert r.status_code == 202, r.text
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "failed" and job["error"]["code"] == code, job
    v = admin_client.get("/admin/datasets/bad-raster/versions/1").json()
    assert v["status"] == "failed" and v["validation"]["ok"] is False and v["validation"]["errors"][0]["code"] == code
    assert "validate" in v["actions"] and "publish" not in v["actions"]
    assert admin_client.post("/admin/datasets/bad-raster/versions/1/publish").status_code == 409
    with db.owner() as conn:
        assert conn.execute("SELECT count(*) AS n FROM catalog.datasets").fetchone()["n"] == 0


def test_failed_upload_is_superseded_by_a_new_file(admin_client, run_ingest, samples):
    r = upload(admin_client, samples["no_crs"], synthetic.raster_meta("fixme", 1))
    wait_job(admin_client, r.json()["job_id"], run_ingest)
    r = upload(admin_client, samples["raster"], synthetic.raster_meta("fixme", 1))
    assert r.status_code == 202
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "completed"


def test_failed_publish_leaves_existing_ready_version(admin_client, run_ingest, samples, db, roots):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("keep-v1", 1))
    r = upload(admin_client, samples["corrupt"], synthetic.raster_meta("keep-v1", 2))
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "failed"
    with db.owner() as conn:
        cur = conn.execute("SELECT version FROM catalog.current_datasets WHERE dataset_id='keep-v1'").fetchone()
    assert cur["version"] == 1 and (roots["assets"] / "rasters/keep-v1/v1/keep-v1_v1.tif").is_file()


def test_bomb_like_rasters_are_refused_before_decoding(admin_client, run_ingest, tmp_path):
    bomb = synthetic.raster_sparse(tmp_path / "bomb.tif", 40000, 40000)  # 1.6e9 cells in a few hundred KB
    assert bomb.stat().st_size < 2_000_000
    r = upload(admin_client, bomb, synthetic.raster_meta("bomb", 1, allow_missing_nodata=True))
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "failed" and job["error"]["code"] in ("SUSPICIOUS_COMPRESSION", "RASTER_TOO_LARGE"), job
    huge = synthetic.raster_sparse(tmp_path / "huge.tif", 60000, 60000)  # 3.6e9 > max_raster_pixels
    r = upload(admin_client, huge, synthetic.raster_meta("huge", 1))
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "failed" and job["error"]["code"] == "RASTER_TOO_LARGE", job


def test_vector_lifecycle_and_invalid_geometry(admin_client, user_client, run_ingest, samples, db):
    meta = synthetic.vector_meta("test-towns", 1, id_property="unit_id", unit_id_prefix="tt:")
    job = publish(admin_client, run_ingest, samples["vector"], meta)
    assert job["result"]["kind"] == "vector"
    with db.owner() as conn:
        units = conn.execute("SELECT unit_id FROM geo.admin_units ORDER BY unit_id").fetchall()
    assert [u["unit_id"] for u in units] == ["tt:town-a", "tt:town-b"]
    assert user_client.get("/api/catalog/datasets/test-towns").json()["kind"] == "vector"

    r = upload(admin_client, samples["vector_invalid"], synthetic.vector_meta("bad-towns", 1))
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "failed" and job["error"]["code"] == "INVALID_GEOMETRY"
    r = upload(admin_client, samples["vector_bad_crs"], synthetic.vector_meta("bad-crs-towns", 1))
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    assert job["status"] == "failed" and job["error"]["code"] == "UNSUPPORTED_CRS"


@pytest.mark.parametrize("meta_patch,code", [
    ({"kind": "vector"}, "INVALID_METADATA"),          # raster bytes, vector metadata
    ({"units": None}, "INVALID_METADATA"),
    ({"dataset_id": "../../etc"}, "INVALID_METADATA"),
    ({"dataset_id": "Upper-Case"}, "INVALID_METADATA"),
    ({"file": "/etc/passwd"}, "INVALID_METADATA"),
    ({"asset_path": "rasters/x/v1/x.tif"}, "INVALID_METADATA"),
    ({"status": "ready"}, "INVALID_METADATA"),
    ({"checksum_sha256": "0" * 64}, "CHECKSUM_MISMATCH"),
    ({"version": 0}, "INVALID_METADATA"),
])
def test_bad_metadata_rejected_and_staging_cleaned(admin_client, samples, roots, meta_patch, code):
    meta = {**synthetic.raster_meta("meta-x", 1), **meta_patch}
    meta = {k: v for k, v in meta.items() if v is not None}
    r = upload(admin_client, samples["raster"], meta)
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert list(roots["staging"].iterdir()) == []


def test_traversal_filename_is_display_only(admin_client, samples, roots, db):
    r = upload(admin_client, samples["raster"], synthetic.raster_meta("trav", 1), filename="../../../../etc/cron.d/x.tif")
    assert r.status_code == 202
    with db.owner() as conn:
        row = conn.execute("SELECT original_filename, staged_name FROM cgis_app.uploads").fetchone()
    assert row["original_filename"] == "x.tif" and row["staged_name"].endswith(".tif") and "/" not in row["staged_name"]
    assert [p.name for p in roots["staging"].iterdir()] == [row["staged_name"]]


@pytest.mark.parametrize("filename,payload,code,status", [
    ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "UNSUPPORTED_MEDIA_TYPE", 415),
    ("x.zip", b"PK\x03\x04" + b"0" * 64, "UNSUPPORTED_MEDIA_TYPE", 415),
    ("x.geojson", b"II*\x00" + b"0" * 64, "UNSUPPORTED_MEDIA_TYPE", 415),
    ("x.tif.gz", b"\x1f\x8b\x08" + b"0" * 64, "UNSUPPORTED_MEDIA_TYPE", 415),
])
def test_wrong_file_types(admin_client, tmp_path, roots, filename, payload, code, status):
    p = tmp_path / "payload"
    p.write_bytes(payload)
    r = upload(admin_client, p, synthetic.raster_meta("types", 1), filename=filename)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert list(roots["staging"].iterdir()) == []


def test_oversize_and_encoded_bodies(admin_client, tmp_path, roots):
    big = tmp_path / "big.tif"
    with open(big, "wb") as fh:
        fh.write(b"II*\x00")
        fh.seek(65 * 1024 * 1024)  # > max_upload_bytes (64 MiB) in the test settings
        fh.write(b"\x00")
    r = upload(admin_client, big, synthetic.raster_meta("big", 1))
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert list(roots["staging"].iterdir()) == []
    r = admin_client.post("/admin/datasets/uploads", content=b"x", headers={"Content-Encoding": "gzip",
                                                                            "Content-Type": "multipart/form-data; b=1"})
    assert r.status_code == 415
    r = admin_client.post("/admin/datasets/uploads", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 415
    r = admin_client.post("/admin/datasets/uploads", content=b"x",
                          headers={"Content-Type": "multipart/form-data; boundary=zz",
                                   "Content-Length": str(10 * 1024**3)})
    assert r.status_code == 413


def test_kind_cannot_change_between_versions(admin_client, run_ingest, samples):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("kindy", 1))
    r = upload(admin_client, samples["vector"], synthetic.vector_meta("kindy", 2))
    assert r.status_code == 409 and r.json()["error"]["code"] == "KIND_CONFLICT"


def test_access_policy_controls_discovery_and_export(admin_client, user_client, anon_client, run_ingest, samples):
    publish(admin_client, run_ingest, samples["raster"], synthetic.raster_meta("policy-x", 1))
    assert anon_client.get("/api/catalog/datasets").json()["total"] == 0  # default access: users
    assert anon_client.get("/api/catalog/datasets/policy-x").status_code == 404
    assert user_client.get("/api/catalog/datasets/policy-x").status_code == 200

    r = admin_client.put("/admin/datasets/policy-x/access", json={"access": "admins", "note": "restricted source"})
    assert r.status_code == 200 and r.json()["access"] == "admins"
    assert user_client.get("/api/catalog/datasets/policy-x").status_code == 404
    assert user_client.get("/api/catalog/datasets").json()["total"] == 0
    r = user_client.post("/api/exports", json={"dataset_id": "policy-x", "kind": "full", "format": "COG"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "DATASET_NOT_FOUND"
    assert admin_client.get("/api/catalog/datasets/policy-x").json()["access"] == "admins"

    assert admin_client.put("/admin/datasets/policy-x/access", json={"access": "public"}).status_code == 200
    listing = anon_client.get("/api/catalog/datasets").json()
    assert listing["total"] == 1 and listing["items"][0]["versions"] == [{"version": 1, "status": "ready"}]
    assert anon_client.post("/api/exports", json={"dataset_id": "policy-x", "kind": "full", "format": "COG"}
                            ).status_code == 401
    assert admin_client.put("/admin/datasets/nope/access", json={"access": "public"}).status_code == 404
    assert admin_client.put("/admin/datasets/policy-x/access", json={"access": "everyone"}).status_code == 422


def test_admin_job_listing(admin_client, run_ingest, samples):
    r = upload(admin_client, samples["raster"], synthetic.raster_meta("jobs-x", 1))
    wait_job(admin_client, r.json()["job_id"], run_ingest)
    listing = admin_client.get("/admin/jobs").json()
    assert listing["total"] >= 1 and listing["items"][0]["kind"] in ("upload", "validate", "publish")
    assert admin_client.get("/admin/jobs?kind=export").status_code == 422
    detail = admin_client.get("/admin/datasets/jobs-x").json()
    assert detail["recent_jobs"] and detail["access"] == "users"


def test_pagination_and_filters(admin_client, run_ingest, samples):
    for i in range(3):
        upload(admin_client, samples["raster"], synthetic.raster_meta(f"page-{i}", 1))
    page = admin_client.get("/admin/datasets?limit=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["links"]["next"].startswith("/admin/datasets?offset=2")
    assert admin_client.get("/admin/datasets?q=page-1").json()["total"] == 1
    assert admin_client.get("/admin/datasets?status=validating").json()["total"] == 3
    assert admin_client.get("/admin/datasets?status=bogus").status_code == 422
