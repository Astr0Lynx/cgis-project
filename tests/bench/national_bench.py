# National-scale HTTP/DB benchmark through the gateway; run by run_national_bench.sh. DB access (PG* env)
# only creates/drops the SYNTHETIC 12 100-unit boundary grid and runs EXPLAIN ANALYZE.
#   python tests/bench/national_bench.py --gateway http://gateway:8080 --raster <id> [--raster <id> ...]
#       [--vector <real boundary dataset id>] --out data/bench/national_bench.json

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid

import httpx
import morecantile
import psycopg

GRID_ID = "bench-india-grid-12100"
CLIP_SAVE_DIR = None  # set from --out: downloaded async clips are kept for the QGIS check
GRID_N = 110                      # 110 x 110 = 12 100 synthetic boundary polygons over India's bbox
INDIA_BBOX = (68.2, 6.8, 97.4, 35.5)


# Milliseconds elapsed since t0. Used by timed, page_all and processing_bench.
def ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 1)


# Calls fn `runs` times; returns ({runs, median_ms, max_ms}, last result). Used by tile_bench and main.
def timed(fn, runs=5):
    samples, out = [], None
    for _ in range(runs):
        t0 = time.monotonic()
        out = fn()
        samples.append(ms(t0))
    return {"runs": runs, "median_ms": statistics.median(samples), "max_ms": max(samples)}, out


# GeoJSON Polygon for a lon/lat rectangle. Used by processing_bench.
def rect(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


# ---------------------------------------------------------------------------------------- vectors
# (Re)creates the SYNTHETIC GRID_N x GRID_N boundary dataset in catalog/geo tables. Used by main.
def make_grid(conn) -> None:
    w, s, e, n = INDIA_BBOX
    dx, dy = (e - w) / GRID_N, (n - s) / GRID_N
    with conn.transaction():
        conn.execute("DELETE FROM geo.admin_units WHERE dataset_id = %s", (GRID_ID,))
        conn.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (GRID_ID,))
        conn.execute(
            "INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, crs, bbox, "
            "checksum_sha256, source, provenance, status) VALUES (%s, 1, %s, 'vector', 'other', 'EPSG:4326', "
            "ST_MakeEnvelope(%s, %s, %s, %s, 4326), repeat('0', 64), 'national-bench', "
            "'{\"synthetic\": true}', 'staged')",
            (GRID_ID, "SYNTHETIC 12 100-cell benchmark grid over India", w, s, e, n))
        conn.execute(
            "INSERT INTO geo.admin_units (unit_id, name, level, geom, dataset_id, dataset_version) "
            "SELECT format('g-%%s-%%s', lpad(i::text, 3, '0'), lpad(j::text, 3, '0')), 'cell', 'bench_grid', "
            "ST_Multi(ST_MakeEnvelope(%s + i * %s, %s + j * %s, %s + (i + 1) * %s, %s + (j + 1) * %s, 4326)), %s, 1 "
            "FROM generate_series(0, %s - 1) i, generate_series(0, %s - 1) j",
            (w, dx, s, dy, w, dx, s, dy, GRID_ID, GRID_N, GRID_N))
        conn.execute("UPDATE catalog.datasets SET status = 'ready' WHERE dataset_id = %s", (GRID_ID,))
    conn.execute("ANALYZE geo.admin_units")


# Deletes the synthetic grid dataset. Used by main unless --keep-grid.
def drop_grid(conn) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM geo.admin_units WHERE dataset_id = %s", (GRID_ID,))
        conn.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (GRID_ID,))


# EXPLAIN ANALYZE of the key vector queries on the grid (time, index use, plan). Used by main.
def explain(conn) -> dict:
    queries = {
        "collection page, deep offset (dataset+version scope, ORDER BY unit_id)":
            ("SELECT unit_id FROM geo.admin_units WHERE dataset_id = %s AND dataset_version = 1 "
             "ORDER BY unit_id OFFSET 12000 LIMIT 100", (GRID_ID,)),
        "collection count (numberMatched)":
            ("SELECT count(*) FROM geo.admin_units WHERE dataset_id = %s AND dataset_version = 1", (GRID_ID,)),
        "bbox on current units (GiST)":
            ("SELECT unit_id FROM geo.current_admin_units WHERE geom && ST_MakeEnvelope(77, 28, 77.5, 28.5, 4326) "
             "AND ST_Intersects(geom, ST_MakeEnvelope(77, 28, 77.5, 28.5, 4326))", ()),
        "geo.units_in_bbox primitive (EPSG:3857 input)":
            ("SELECT unit_id FROM geo.units_in_bbox(8571000, 3250000, 8627000, 3315000, 3857)", ()),
    }
    out = {}
    for label, (sql, params) in queries.items():
        rows = conn.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON) " + sql, params).fetchall()
        plan = "\n".join(r[0] for r in rows)
        exec_ms = next((float(line.split(":")[1].split()[0]) for line in plan.splitlines()
                        if "Execution Time" in line), None)
        out[label] = {"execution_ms": exec_ms, "uses_index": ("Index" in plan), "plan": plan}
    return out


# Polls OGC /collections until dataset_id appears (True) or timeout (False). Used by main.
def wait_published(http, ogc, dataset_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = {c["id"] for c in http.get(f"{ogc}/collections?f=json").json()["collections"]}
        if dataset_id in ids:
            return True
        time.sleep(2)
    return False


# Follows every OGC items "next" link; reports completeness and page latency. Used by main.
def page_all(http, ogc, dataset_id, limit=1000) -> dict:
    url = f"{ogc}/collections/{dataset_id}/items?f=json&limit={limit}&skipGeometry=true"
    seen, pages, lat = [], 0, []
    matched = None
    while url:
        t0 = time.monotonic()
        doc = http.get(url).json()
        lat.append(ms(t0))
        matched = doc.get("numberMatched", matched)
        seen += [f["id"] for f in doc["features"]]
        pages += 1
        nxt = [lk["href"] for lk in doc["links"] if lk["rel"] == "next"]
        url = nxt[0] if nxt else None
    return {"numberMatched": matched, "features_paged": len(seen), "unique": len(set(seen)),
            "complete": matched == len(seen) == len(set(seen)), "pages": pages,
            "page_median_ms": statistics.median(lat), "page_max_ms": max(lat)}


# ---------------------------------------------------------------------------------------- rasters
# Times OGC PNG tiles over Delhi at several zooms plus one vendor tile. Used by main per --raster.
def tile_bench(http, ogc, tiles, dataset_id) -> dict:
    tms = morecantile.tms.get("WebMercatorQuad")
    out = {}
    style = http.get(f"{ogc}/collections/{dataset_id}/tiles/WebMercatorQuad?f=json").json().get("style", {})
    out["ogc_style_title"] = style.get("title")
    out["ogc_style_shared"] = "configured shared range" in style.get("description", "")
    for z in (3, 5, 7, 9, 11):
        t = tms.tile(77.21, 28.61, z)           # Delhi
        url = f"{ogc}/collections/{dataset_id}/tiles/WebMercatorQuad/{t.z}/{t.y}/{t.x}?f=png"
        stats, resp = timed(lambda: http.get(url), runs=3)
        out[f"ogc_png_z{z}"] = {**stats, "status": resp.status_code, "bytes": len(resp.content)}
    t = tms.tile(77.21, 28.61, 9)
    vurl = f"{tiles}/datasets/{dataset_id}/tiles/WebMercatorQuad/9/{t.x}/{t.y}.png?rescale=0,50"
    stats, resp = timed(lambda: http.get(vurl), runs=3)
    out["vendor_png_z9"] = {**stats, "status": resp.status_code}
    return out


# Polls a job until completed/failed or timeout; returns (job, seconds, last progress values).
# Used by processing_bench.
def poll(http, api, job_id, timeout=3600):
    t0 = time.monotonic()
    progress = []
    while True:
        job = http.get(f"{api}/operations/{job_id}").json()
        progress.append(job.get("progress"))
        if job["status"] in ("completed", "failed") or time.monotonic() - t0 > timeout:
            return job, round(time.monotonic() - t0, 1), [p for p in progress if p is not None][-5:]
        time.sleep(1)


# Zonal stats (district sync/async, state, India), async COG clip (saved for the QGIS check) and job
# cancellation for one raster. Used by main per --raster.
def processing_bench(http, api, dataset_id) -> dict:
    out = {}
    district = rect(77.0, 28.4, 77.35, 28.85)          # ~ Delhi NCT extent (0.35 x 0.45 deg)
    state = rect(77.0, 23.8, 84.6, 30.4)               # ~ Uttar Pradesh bounding box (7.6 x 6.6 deg)
    india = rect(*INDIA_BBOX)
    body = {"dataset_id": dataset_id, "aoi_crs": "EPSG:4326"}

    t0 = time.monotonic()
    r = http.post(f"{api}/operations/zonal-statistics", json={**body, "aoi": district})
    out["zonal_district_sync"] = {"ms": ms(t0), "status": r.status_code,
                                  "sum": r.json().get("sum"), "count_valid": r.json().get("count_valid")}
    r = http.post(f"{api}/operations/zonal-statistics", json={**body, "aoi": district, "mode": "async"})
    job, secs, prog = poll(http, api, r.json()["job_id"])
    res = job.get("result") or {}
    out["zonal_district_async"] = {"status": job["status"], "seconds": secs, "sum": res.get("sum"),
                                   "count_valid": res.get("count_valid"),
                                   "matches_sync": res.get("count_valid") == out["zonal_district_sync"]["count_valid"]
                                   and abs((res.get("sum") or 0) - (out["zonal_district_sync"]["sum"] or 0))
                                   <= 1e-9 * max(1.0, abs(out["zonal_district_sync"]["sum"] or 0))}
    r = http.post(f"{api}/operations/zonal-statistics", json={**body, "aoi": state})
    err = r.json().get("error", {})
    out["zonal_state_sync"] = {"status": r.status_code, "code": err.get("code"),
                               "pixels": (err.get("details") or {}).get("pixels"),
                               "hint": (err.get("details") or {}).get("hint")}
    for label, aoi in (("zonal_state_async", state), ("zonal_india_async", india)):
        r = http.post(f"{api}/operations/zonal-statistics", json={**body, "aoi": aoi, "mode": "auto"})
        job, secs, prog = poll(http, api, r.json()["job_id"])
        res = job.get("result") or {}
        out[label] = {"http_status": r.status_code, "status": job["status"], "seconds": secs,
                      "count_valid": res.get("count_valid"), "pixels_in_aoi": res.get("pixels_in_aoi"),
                      "sum": res.get("sum"), "processing": res.get("processing"), "error": job.get("error"),
                      "last_progress": prog}
    clip_aoi = rect(76.8, 28.2, 77.8, 29.0)            # ~1 x 0.8 deg around Delhi
    r = http.post(f"{api}/operations/clip", json={**body, "aoi": clip_aoi, "mode": "async", "format": "COG"})
    job, secs, _ = poll(http, api, r.json()["job_id"])
    res = job.get("result") or {}
    dl = http.get(f"{api}/downloads/{job['job_id']}") if job["status"] == "completed" else None
    if dl is not None and dl.status_code == 200 and CLIP_SAVE_DIR:
        with open(os.path.join(CLIP_SAVE_DIR, f"clip_async_{dataset_id}.tif"), "wb") as fh:
            fh.write(dl.content)
    out["clip_async_cog"] = {"status": job["status"], "seconds": secs, "output": res.get("output"),
                             "download_status": dl.status_code if dl else None,
                             "download_bytes": len(dl.content) if dl else None, "error": job.get("error")}
    # cancellation of a long job
    r = http.post(f"{api}/operations/zonal-statistics", json={**body, "aoi": india, "mode": "async"})
    jid = r.json()["job_id"]
    time.sleep(3)
    c = http.post(f"{api}/operations/{jid}/cancel")
    job, secs, _ = poll(http, api, jid, timeout=120)
    out["cancel_india_job"] = {"cancel_http": c.status_code, "final_status": job["status"],
                               "error_code": (job.get("error") or {}).get("code"), "seconds_to_stop": secs}
    return out


# Parses args, runs the grid/vector/raster benchmarks and writes the JSON report to --out.
# Used by run_national_bench.sh step "HTTP/DB benchmark through the gateway".
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://gateway:8080")
    ap.add_argument("--raster", action="append", default=[])
    ap.add_argument("--vector", action="append", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-grid", action="store_true")
    args = ap.parse_args(argv)
    global CLIP_SAVE_DIR
    CLIP_SAVE_DIR = os.path.dirname(os.path.abspath(args.out))
    gw = args.gateway.rstrip("/")
    ogc, tiles, api = f"{gw}/ogc", f"{gw}/tiles", f"{gw}/api"
    report = {"run_id": uuid.uuid4().hex[:8], "gateway": gw, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with httpx.Client(timeout=600) as http, psycopg.connect("", autocommit=True) as conn:
        make_grid(conn)
        report["vector_grid"] = {"dataset_id": GRID_ID, "units": GRID_N * GRID_N, "explain": explain(conn)}
        if wait_published(http, ogc, GRID_ID):
            report["vector_grid"]["paging"] = page_all(http, ogc, GRID_ID)
            stats, resp = timed(lambda: http.get(f"{ogc}/collections/{GRID_ID}/items",
                                                 params={"f": "json", "bbox": "77,28,77.5,28.5", "limit": 100}))
            report["vector_grid"]["bbox_query"] = {**stats, "numberMatched": resp.json().get("numberMatched")}
            stats, resp = timed(lambda: http.get(f"{ogc}/collections/{GRID_ID}/items/g-055-055?f=json"))
            report["vector_grid"]["item_lookup"] = {**stats, "status": resp.status_code}
        for vid in args.vector:
            entry = {"paging": page_all(http, ogc, vid, limit=100)}
            first = http.get(f"{ogc}/collections/{vid}/items?f=json&limit=1").json()["features"][0]["id"]
            stats, resp = timed(lambda: http.get(f"{ogc}/collections/{vid}/items/{first}?f=json"))
            entry["item_lookup"] = {**stats, "status": resp.status_code}
            report.setdefault("vector_real", {})[vid] = entry
        for rid in args.raster:
            report.setdefault("rasters", {})[rid] = {
                "tiles": tile_bench(http, ogc, tiles, rid),
                "metadata": http.get(f"{tiles}/datasets/{rid}").json(),
                "processing": processing_bench(http, api, rid),
            }
        if not args.keep_grid:
            drop_grid(conn)
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != "vector_grid"}, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
