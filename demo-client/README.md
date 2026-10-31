# `demo-client/`: diagnostic viewer

**Owner:** Agent 6 (integration).

This is a small MapLibre GL JS page served by the `viewer` service at `http://localhost:8080/viewer/`.
It is a **test harness for Phase 2**, not the Phase 4 application: there are no accounts, no
dataset management and no styling work. All data shown is **SYNTHETIC**.

## What it does

| Feature | Endpoint it uses | Standard? |
|---|---|---|
| Lists the layers it can show | `GET /ogc/collections?f=json` | OGC API - Common/Features |
| Draws boundaries | `GET /ogc/collections/{id}/items?f=json` | OGC API - Features |
| Draws rasters (default source) | `GET /ogc/collections/{id}/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png` | OGC API - Tiles |
| Draws rasters (alternative source) | `GET /tiles/datasets/{id}/tiles/WebMercatorQuad/{z}/{x}/{y}.png` | **Vendor-specific (TiTiler)** |
| Sets the zoom range | `tileMatrixSetLimits` (OGC) or TileJSON `minzoom`/`maxzoom` (vendor) | as above |
| Shows the pixel value under a click | `GET /tiles/datasets/{id}/point/{lon},{lat}` | Vendor-specific |
| Zonal statistics | `POST /api/operations/zonal-statistics` | Custom API |
| Clip, then download the GeoTIFF | `POST /api/operations/clip`, `GET /api/downloads/{job_id}` | Custom API |

How it behaves:

- **No hard-coded data.** Layers are discovered from the OGC API. Raster collections are those
  with a tiles link, and vector collections are those with an items link. Neither the code nor
  the page names a dataset id, so real Phase 1 data appears without code changes.
- **Choosing an area.** Click a boundary to use it as the area of interest (AOI), or use the
  current map view. A view that extends past the raster demonstrates the API's explicit errors.
- **Statistics are server-side.** The pixel-centre rule applies (`all_touched=false`), and the
  page displays the server's answer unchanged.

## Files

- `index.html`: the page. MapLibre 4.7.1 loads from jsDelivr with Subresource Integrity. This is
  the only external request; every data request goes to the same gateway.
- `app.js`: the logic.
- `style.css`: the styles.

The image is built by the `viewer` service in `compose.yaml` (nginx, using `infra/viewer/default.conf`).

## Tests

- `tests/e2e/test_60_viewer.py` checks the page, assets and wiring over HTTP.
- `tests/e2e/browser/check_viewer.py`, run by `sh tests/e2e/run_e2e.sh --browser`, drives the page
  in headless Chromium. It waits until the tiles have loaded, clicks Town A, runs zonal statistics
  (sum 100 for 2001 and 200 for 2011), clips and downloads the GeoTIFF, switches to the vendor
  tiles, and fails on any console error or failed request.
