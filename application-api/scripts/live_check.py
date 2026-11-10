"""End-to-end check against a RUNNING stack (gateway + app-api + workers + OGC), using the bootstrap accounts
from the root .env. Prints a JSON report; never prints passwords or tokens.

    python application-api/scripts/live_check.py [--base http://localhost:8080] [--raster data/real-samples/ind_ppp_2011_1km_Aggregated.tif]

Steps: anonymous/user/admin authorization on real routes; admin upload -> validate -> publish of a real GeoTIFF;
OGC API collection discovery (waits for the publisher refresh); personal access token use; native and clipped
exports with checksum verification; cleanup of nothing (the published dataset stays; version is auto-assigned).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def poll(fn, timeout=300, every=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = fn()
        if out is not None:
            return out
        time.sleep(every)
    raise TimeoutError("timed out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--raster", default=str(REPO / "data/real-samples/ind_ppp_2011_1km_Aggregated.tif"))
    ap.add_argument("--dataset-id", default="live-check-worldpop-1km-ind-2011")
    args = ap.parse_args()
    env = read_env(REPO / ".env")
    B = args.base
    report: dict = {"base": B, "steps": []}

    def step(name, ok, **info):
        report["steps"].append({"step": name, "ok": bool(ok), **info})
        if not ok:
            print(json.dumps(report, indent=2, default=str))
            sys.exit(1)

    anon = httpx.Client(timeout=120)
    step("gateway health", anon.get(f"{B}/healthz").status_code == 200)
    step("app ready", anon.get(f"{B}/api/app/ready").json()["ready"] is True)
    step("anonymous /auth/me is 200", anon.get(f"{B}/auth/me").json()["authenticated"] is False)
    step("anonymous admin list is 401", anon.get(f"{B}/admin/datasets").status_code == 401)
    step("anonymous export is 401", anon.post(f"{B}/api/exports", json={"dataset_id": "x", "kind": "full",
                                                                      "format": "COG"}).status_code == 401)

    def login(user, pw):
        c = httpx.Client(timeout=600)
        r = c.post(f"{B}/auth/login", json={"username": user, "password": pw}, headers={"Origin": B})
        step(f"login {user}", r.status_code == 200, role=r.json().get("user", {}).get("role"))
        c.headers.update({"X-CSRF-Token": r.json()["csrf_token"], "Origin": B})
        return c

    admin = login(env["APP_BOOTSTRAP_ADMIN_USERNAME"], env["APP_BOOTSTRAP_ADMIN_PASSWORD"])
    viewer = login(env["APP_BOOTSTRAP_USER_USERNAME"], env["APP_BOOTSTRAP_USER_PASSWORD"])
    step("viewer admin list is 403", viewer.get(f"{B}/admin/datasets").status_code == 403)
    step("viewer upload is 403", viewer.post(f"{B}/admin/datasets/uploads", files={"file": ("x.tif", b"II*\x00")})
         .status_code == 403)

    raster = Path(args.raster)
    sha = hashlib.sha256(raster.read_bytes()).hexdigest()
    meta = {"dataset_id": args.dataset_id, "kind": "raster", "product_level": "source", "year": 2011,
            "title": "WorldPop 2011 India 1 km (live check, real sample)", "units": "people_per_cell",
            "source": "WorldPop Global 2000-2020 1km unconstrained, IND 2011", "checksum_sha256": sha,
            "provenance": {"license": "CC BY 4.0", "synthetic": False}}
    t = time.monotonic()
    with open(raster, "rb") as fh:
        r = admin.post(f"{B}/admin/datasets/uploads", files={"file": (raster.name, fh, "image/tiff"),
                                                             "metadata": (None, json.dumps(meta))})
    step("admin upload through gateway", r.status_code in (200, 202), status=r.status_code,
         outcome=(r.json().get("result") or {}).get("outcome"), seconds=round(time.monotonic() - t, 2))
    job = r.json()
    version = job["version"]
    if job["status"] != "completed" or (job.get("result") or {}).get("outcome") != "unchanged":
        job = poll(lambda: (lambda j: j if j["status"] not in ("queued", "running") else None)(
            admin.get(f"{B}/admin/jobs/{job['job_id']}").json()))
        step("validation in ingest-worker container", job["status"] == "completed", code=(job.get("error") or {}).get("code"))
        t = time.monotonic()
        r = admin.post(f"{B}/admin/datasets/{args.dataset_id}/versions/{version}/publish")
        step("publish accepted", r.status_code == 202)
        job = poll(lambda: (lambda j: j if j["status"] not in ("queued", "running") else None)(
            admin.get(f"{B}/admin/jobs/{r.json()['job_id']}").json()))
        step("publish in ingest-worker container", job["status"] == "completed", outcome=(job.get("result") or {}).get(
            "outcome"), seconds=round(time.monotonic() - t, 2))

    t = time.monotonic()
    coll = poll(lambda: (lambda r: r.json() if r.status_code == 200 else None)(
        anon.get(f"{B}/ogc/collections/{args.dataset_id}", params={"f": "json"})), timeout=180, every=3)
    step("OGC API collection discovered", coll.get("id") == args.dataset_id, seconds=round(time.monotonic() - t, 1))
    step("catalog visible to viewer", viewer.get(f"{B}/api/catalog/datasets/{args.dataset_id}").status_code == 200)
    step("catalog hidden from anonymous (default access users)",
         anon.get(f"{B}/api/catalog/datasets/{args.dataset_id}").status_code == 404)

    tok = viewer.post(f"{B}/auth/tokens", json={"name": "live-check", "expires_in_days": 1}).json()
    pat = httpx.Client(timeout=600, auth=("any", tok["token"]))
    step("token (HTTP Basic) can list exports", pat.get(f"{B}/api/exports").status_code == 200)

    native = viewer.post(f"{B}/api/exports", json={"dataset_id": args.dataset_id, "kind": "full",
                                                   "format": "COG"}).json()
    h = hashlib.sha256()
    t = time.monotonic()
    with pat.stream("GET", f"{B}{native['links']['download']}") as resp:
        for chunk in resp.iter_bytes():
            h.update(chunk)
    step("native COG download (token) checksum", h.hexdigest() == native["result"]["sha256"],
         bytes=native["result"]["size_bytes"], seconds=round(time.monotonic() - t, 2))

    aoi = {"type": "Polygon", "coordinates": [[[78.30, 17.25], [78.65, 17.25], [78.65, 17.55], [78.30, 17.55],
                                               [78.30, 17.25]]]}
    r = viewer.post(f"{B}/api/exports", json={"dataset_id": args.dataset_id, "kind": "clip", "aoi": aoi,
                                              "aoi_crs": "EPSG:4326", "format": "GTiff", "output_crs": "EPSG:32644"})
    step("clip export accepted", r.status_code == 202)
    clip = poll(lambda: (lambda j: j if j["status"] not in ("queued", "running") else None)(
        viewer.get(f"{B}/api/exports/{r.json()['job_id']}").json()))
    data = viewer.get(f"{B}{clip['links']['download']}").content if clip["status"] == "completed" else b""
    step("clip in export-worker container", clip["status"] == "completed" and
         hashlib.sha256(data).hexdigest() == clip["result"]["sha256"], output_crs=clip["result"]["output"]["crs"],
         native_count_valid=clip["result"]["output"].get("native_count_valid"))
    step("legacy /api/operations/clip still served by processing",
         anon.post(f"{B}/api/operations/clip", json={}).status_code == 422)
    viewer.delete(f"{B}/auth/tokens/{tok['token_id']}")
    step("revoked token refused", pat.get(f"{B}/api/exports").status_code == 401)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
