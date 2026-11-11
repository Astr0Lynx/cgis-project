"""Records REAL request/response pairs of the main admin/user flows into docs/examples/ (APP_EXAMPLES_OUT=dir).
Volatile values (ids, times, tokens, checksums) are replaced by stable placeholders so the files diff cleanly."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import synthetic
from conftest import upload, wait_job

pytestmark = pytest.mark.db

AOI = {"type": "Polygon", "coordinates": [[[78.0101, 17.9801], [78.0302, 17.9801], [78.0302, 17.9902],
                                           [78.0101, 17.9902], [78.0101, 17.9801]]]}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
_SHA = re.compile(r"\b[0-9a-f]{64}\b")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?")
_TOKENS = re.compile(r"cgis_(s|b|pat)_[A-Za-z0-9_-]{43}")


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "<csrf-token>")
    text = _TOKENS.sub(lambda m: f"cgis_{m.group(1)}_<secret>", text)
    text = _SHA.sub("<sha256>", text)
    text = _UUID.sub("<uuid>", text)
    text = _HEX32.sub("<job-hex>", text)
    return _TS.sub("<timestamp>", text)


class Recorder:
    def __init__(self):
        self.items: list[dict] = []
        self.secrets: list[str] = []

    def add(self, name: str, method: str, path: str, resp, body=None, note: str | None = None):
        try:
            payload = resp.json()
        except ValueError:
            payload = f"<{len(resp.content)} bytes {resp.headers.get('content-type')}>"
        keep = ("content-type", "content-disposition", "retry-after", "www-authenticate", "x-checksum-sha256",
                "repr-digest", "set-cookie", "accept-ranges", "content-range")
        self.items.append({"name": name, "request": {"method": method, "path": path, "body": body}, "note": note,
                           "response": {"status": resp.status_code,
                                        "headers": {k: v for k, v in resp.headers.items() if k in keep},
                                        "body": payload}})

    def write(self, out: Path):
        out.mkdir(parents=True, exist_ok=True)
        for i, item in enumerate(self.items, 1):
            text = _scrub(json.dumps(item, indent=2, default=str), self.secrets)
            (out / f"{i:02d}_{item['name']}.json").write_text(text + "\n")


def test_record_examples(admin_client, user_client, anon_client, run_ingest, run_export, samples):
    out = os.environ.get("APP_EXAMPLES_OUT")
    rec = Recorder()
    rec.secrets += [admin_client.headers["X-CSRF-Token"], user_client.headers["X-CSRF-Token"]]

    r = anon_client.get("/auth/me")
    rec.add("auth_me_anonymous", "GET", "/auth/me", r)
    body = {"username": "bob-user", "password": "<password>"}
    r = anon_client.post("/auth/login", json={"username": "bob-user", "password": "wrong-password-1"})
    rec.add("auth_login_wrong_password", "POST", "/auth/login", r, body)
    r = user_client.get("/auth/me")
    rec.add("auth_me_user_cookie_session", "GET", "/auth/me", r)
    r = user_client.post("/auth/tokens", json={"name": "QGIS desktop", "expires_in_days": 30})
    rec.add("auth_create_personal_access_token", "POST", "/auth/tokens", r, {"name": "QGIS desktop",
                                                                            "expires_in_days": 30})

    meta = synthetic.raster_meta("example-pop-2011", 1)
    r = anon_client.post("/admin/datasets/uploads")
    rec.add("admin_upload_anonymous_401", "POST", "/admin/datasets/uploads", r)
    r = user_client.post("/admin/datasets/uploads")
    rec.add("admin_upload_user_403", "POST", "/admin/datasets/uploads", r)
    r = upload(admin_client, samples["raster"], meta)
    rec.add("admin_upload_accepted_202", "POST", "/admin/datasets/uploads", r,
            {"multipart": {"file": "<GeoTIFF bytes>", "metadata": meta}})
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    rec.add("admin_job_validated", "GET", "/admin/jobs/{job_id}", admin_client.get(f"/admin/jobs/{job['job_id']}"))
    r = upload(admin_client, samples["raster"], meta)
    rec.add("admin_upload_same_file_again_200", "POST", "/admin/datasets/uploads", r)
    r = admin_client.post("/admin/datasets/example-pop-2011/versions/1/publish")
    rec.add("admin_publish_202", "POST", "/admin/datasets/example-pop-2011/versions/1/publish", r)
    wait_job(admin_client, r.json()["job_id"], run_ingest)
    rec.add("admin_job_published", "GET", "/admin/jobs/{job_id}", admin_client.get(f"/admin/jobs/{r.json()['job_id']}"))
    r = upload(admin_client, samples["raster_v2"], meta)
    rec.add("admin_upload_conflicting_checksum_409", "POST", "/admin/datasets/uploads", r)
    r = upload(admin_client, samples["no_crs"], synthetic.raster_meta("example-bad", 1))
    job = wait_job(admin_client, r.json()["job_id"], run_ingest)
    rec.add("admin_job_validation_failed_missing_crs", "GET", "/admin/jobs/{job_id}",
            admin_client.get(f"/admin/jobs/{job['job_id']}"))
    rec.add("admin_dataset_list", "GET", "/admin/datasets?limit=10", admin_client.get("/admin/datasets?limit=10"))
    rec.add("admin_dataset_detail", "GET", "/admin/datasets/example-pop-2011",
            admin_client.get("/admin/datasets/example-pop-2011"))

    rec.add("catalog_list_user", "GET", "/api/catalog/datasets", user_client.get("/api/catalog/datasets"))
    rec.add("catalog_list_anonymous_default_policy", "GET", "/api/catalog/datasets",
            anon_client.get("/api/catalog/datasets"))
    rec.add("exports_capabilities", "GET", "/api/exports/capabilities", anon_client.get("/api/exports/capabilities"))
    b = {"dataset_id": "example-pop-2011", "kind": "full", "format": "COG"}
    r = user_client.post("/api/exports", json=b)
    rec.add("export_full_native_201", "POST", "/api/exports", r, b)
    rec.add("export_download_head", "HEAD", "/api/exports/{job_id}/download",
            user_client.head(r.json()["links"]["download"]))
    b = {"dataset_id": "example-pop-2011", "kind": "clip", "aoi": AOI, "aoi_crs": "EPSG:4326", "format": "GTiff",
         "output_crs": "EPSG:32644"}
    r = user_client.post("/api/exports", json=b)
    rec.add("export_clip_queued_202", "POST", "/api/exports", r, b)
    run_export()
    rec.add("export_clip_completed", "GET", "/api/exports/{job_id}", user_client.get(f"/api/exports/{r.json()['job_id']}"))
    b = {"dataset_id": "example-pop-2011", "kind": "full", "format": "PNG"}
    rec.add("export_unsupported_format_422", "POST", "/api/exports", user_client.post("/api/exports", json=b), b)
    rec.add("export_anonymous_401", "POST", "/api/exports",
            anon_client.post("/api/exports", json={"dataset_id": "example-pop-2011", "kind": "full", "format": "COG"}))
    no_csrf = user_client.headers.pop("X-CSRF-Token")
    rec.add("export_cookie_without_csrf_403", "POST", "/api/exports", user_client.post("/api/exports", json=b), b)
    user_client.headers["X-CSRF-Token"] = no_csrf
    if out:
        rec.write(Path(out))
    assert len(rec.items) >= 25
