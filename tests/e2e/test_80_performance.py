# Informational latency measurements on the synthetic fixtures, written to e2e/perf.json.
# A 64x64 raster on one machine: shows interactive response times, NOT a benchmark of real data.

from __future__ import annotations

import json
import statistics
import time

import morecantile

from conftest import API, OGC, RASTER_2001, TILES, TOWN_A, TOWNS, ogc_max_zoom

RUNS = 15


# Calls fn once to warm up, then RUNS times, and returns median/p95/max in ms. Used by test_record_latencies.
def measure(fn):
    fn()                                   # warm-up (caches, connection pool)
    samples = []
    for _ in range(RUNS):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return {"runs": RUNS, "median_ms": round(statistics.median(samples), 1),
            "p95_ms": round(samples[int(0.95 * (RUNS - 1))], 1), "max_ms": round(samples[-1], 1)}


# Records latencies for the main read and processing routes; every p95 is under 10 s.
def test_record_latencies(http, state_dir):
    t = morecantile.tms.get("WebMercatorQuad").tile(78.015, 17.985, ogc_max_zoom(http, RASTER_2001))
    zonal = {"dataset_id": RASTER_2001, "aoi": TOWN_A, "aoi_crs": "EPSG:4326"}

    def ok(resp):
        assert resp.status_code in (200, 201), resp.text
        return resp

    results = {
        "ogc_items_all": measure(lambda: ok(http.get(f"{OGC}/collections/{TOWNS}/items", params={"f": "json"}))),
        "ogc_items_bbox": measure(lambda: ok(http.get(f"{OGC}/collections/{TOWNS}/items",
                                                       params={"f": "json", "bbox": "78.009,17.979,78.021,17.991"}))),
        "ogc_tile_png_deepest": measure(lambda: ok(http.get(
            f"{OGC}/collections/{RASTER_2001}/tiles/WebMercatorQuad/{t.z}/{t.y}/{t.x}", params={"f": "png"}))),
        "vendor_tile_png_same_tile": measure(lambda: ok(http.get(
            f"{TILES}/datasets/{RASTER_2001}/tiles/WebMercatorQuad/{t.z}/{t.x}/{t.y}.png"))),
        "zonal_statistics_town_a": measure(lambda: ok(http.post(f"{API}/operations/zonal-statistics", json=zonal))),
        "clip_town_a_geotiff": measure(lambda: ok(http.post(f"{API}/operations/clip", json=zonal))),
    }
    (state_dir / "perf.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    assert all(r["p95_ms"] < 10_000 for r in results.values())
