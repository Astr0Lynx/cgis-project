"""Drive the diagnostic viewer in headless Chromium (Playwright) and check it end to end.

Run inside the Compose network (see run_e2e.sh --browser):
    python check_viewer.py http://gateway:8080/viewer/ /out

Checks: the page discovers collections, the raster and boundary sources finish loading, no
console errors or failed requests occur, clicking Town A selects it, and the Zonal statistics and
Clip buttons return the known SYNTHETIC answers. Writes screenshots and a JSON result to /out.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://gateway:8080/viewer/"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/out")
OUT.mkdir(parents=True, exist_ok=True)

result = {"url": URL, "checks": {}, "console_errors": [], "failed_requests": []}


def check(name, ok, detail=None):
    result["checks"][name] = {"ok": bool(ok), "detail": detail}
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail is not None else ""))


def wait_idle(page):
    page.wait_for_function(
        "() => typeof map !== 'undefined' && map.getSource('raster') && map.loaded() && map.areTilesLoaded()"
        " && map.isSourceLoaded('raster')", timeout=60000)


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                      "--ignore-gpu-blocklist"])
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.on("console", lambda m: m.type == "error" and result["console_errors"].append(m.text))
    page.on("pageerror", lambda e: result["console_errors"].append(str(e)))
    page.on("response", lambda r: r.status >= 400 and result["failed_requests"].append([r.status, r.url]))

    page.goto(URL, wait_until="networkidle")
    page.wait_for_function("() => document.getElementById('output').textContent.includes('collections')",
                           timeout=30000)
    wait_idle(page)
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "viewer-ogc-tiles.png"))

    rasters = page.eval_on_selector_all("#raster option", "opts => opts.map(o => o.value)")
    check("raster collections discovered", len(rasters) >= 2, rasters)
    vectors = page.inner_text("#vectors")
    check("boundary collection loaded via OGC API Features", "2 features" in vectors, vectors.strip())
    tile_url = page.evaluate("() => map.getSource('raster').tiles[0]")
    check("raster uses OGC API - Tiles template", "/ogc/collections/" in tile_url and "{z}/{y}/{x}" in tile_url, tile_url)
    zoom = page.evaluate("() => [map.getSource('raster').minzoom, map.getSource('raster').maxzoom]")
    check("raster zoom range taken from tileMatrixSetLimits", zoom[1] == 13, zoom)

    # Click the centre of Town A (78.015, 17.985) on the map.
    box = page.locator("#map").bounding_box()
    pt = page.evaluate("() => map.project([78.015, 17.985])")
    page.mouse.click(box["x"] + pt["x"], box["y"] + pt["y"])
    page.wait_for_function("() => document.getElementById('aoi-label').textContent.includes('Town A')", timeout=10000)
    check("clicking Town A selects it as AOI", True, page.inner_text("#aoi-label"))
    page.wait_for_function("() => document.getElementById('pixel').textContent.includes('[1')", timeout=10000)
    check("pixel readout via TiTiler /point", True, page.inner_text("#pixel"))

    page.click("#zonal")
    page.wait_for_function("() => document.getElementById('output').textContent.includes('\"count_valid\"')",
                           timeout=20000)
    zonal = json.loads(page.inner_text("#output"))["response"]
    check("zonal statistics Town A 2001 = 100", zonal.get("sum") == 100 and zonal.get("count_valid") == 100,
          {k: zonal.get(k) for k in ("count_valid", "sum", "mean", "units")})

    page.select_option("#raster", rasters[-1])
    wait_idle(page)
    page.click("#zonal")
    page.wait_for_function(
        f"() => document.getElementById('output').textContent.includes('\"dataset_id\": \"{rasters[-1]}\"')",
        timeout=20000)
    zonal2 = json.loads(page.inner_text("#output"))["response"]
    check(f"zonal statistics Town A {rasters[-1]} = 200", zonal2.get("sum") == 200, zonal2.get("sum"))

    page.click("#clip")
    page.wait_for_selector("[data-testid=download-link]", timeout=20000)
    href = page.get_attribute("[data-testid=download-link]", "href")
    with page.expect_download() as dl_info:
        page.click("[data-testid=download-link]")
    download = dl_info.value
    target = OUT / download.suggested_filename
    download.save_as(str(target))
    check("clip download is a GeoTIFF", target.read_bytes()[:4] in (b"II*\x00", b"MM\x00*"),
          {"href": href, "file": download.suggested_filename, "bytes": target.stat().st_size})

    page.check("input[name=source][value=vendor]")
    wait_idle(page)
    page.wait_for_timeout(500)
    vendor_url = page.evaluate("() => map.getSource('raster').tiles[0]")
    check("vendor TiTiler source switch works", "/tiles/datasets/" in vendor_url, vendor_url)
    page.screenshot(path=str(OUT / "viewer-vendor-tiles-after-clip.png"))

    check("no console errors", not result["console_errors"], result["console_errors"])
    check("no failed HTTP requests", not result["failed_requests"], result["failed_requests"])
    browser.close()

result["ok"] = all(c["ok"] for c in result["checks"].values())
(OUT / "viewer-check.json").write_text(json.dumps(result, indent=2))
print("RESULT", "OK" if result["ok"] else "FAILED")
sys.exit(0 if result["ok"] else 1)
