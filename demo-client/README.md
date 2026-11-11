# `demo-client/`: CyberGIS viewer

**Owner:** Agent 6 (completion branch `completion/agent6-webapp`).

The application layer from page 2 of the project flowchart: **layer-wise visualisation**, **role-based
access** (an admin can post datasets; a user can only get data) and **downloads** where the user picks the
dataset, the CRS and the geo-format. It is the Phase 2 MapLibre viewer, extended in place. It is served
by the `viewer` service at `http://localhost:8080/viewer/`, needs no build step, and keeps every Phase 2
behaviour and route.

Layers marked **SYNTHETIC** are deterministic test fixtures, not population estimates. A badge in the top
bar always shows the active layer's provenance: *SYNTHETIC test data*, *Real data · provenance recorded*,
or *Provenance not exposed*.

## What it does

| Area | What the user gets | Interface used | Standard? |
|---|---|---|---|
| Discovery | Dataset browser grouped by product level. Time series (e.g. the 11 Level 1 years) are detected from `product_level`/`units`/`source`/`year`. Level 2 towns form one logical group (`provenance.group_id`). Each layer has a metadata panel (id, version, year, units, CRS, NoData, cell size, source, license, provenance) | `GET /ogc/collections`, vendor `GET /tiles/datasets`, optional catalog metadata | OGC API – Common/Features; TiTiler is vendor-specific |
| Rasters | OGC API – Tiles by default. TiTiler can be chosen and is labelled vendor-specific. Zoom limits come from `tileMatrixSetLimits` / TileJSON | `/ogc/collections/{id}/tiles/WebMercatorQuad/{z}/{y}/{x}`, `/tiles/datasets/{id}/tiles/WebMercatorQuad/{z}/{x}/{y}.png` | OGC API – Tiles / vendor |
| Time | Year slider and year chips. Play animation. Rapid changes cancel cleanly | as above | |
| Legends | Ramp, ticks and NoData swatch for every layer. Warns when years use different published ranges. One click applies a **shared range** across compatible layers: same units and same support, derived from the server's own statistics | OGC tileset `style`; vendor `/statistics` and `rescale` | OGC style / vendor |
| Compare | Swipe (keyboard-operable divider) or side-by-side. A and B are always labelled. Notes explain when raw pixel values are not comparable (cell size, units) or when provenance differs | | |
| Level 2 | Overlay every town on the national map, with extent markers and an honest resolution note | | |
| Boundaries | Viewport-scoped, paginated loading that follows `rel=next`. A per-view budget shows "zoom in" instead of silently truncating | `GET /ogc/collections/{id}/items?bbox=…&limit=500` | OGC API – Features |
| Search | State / district / town search. Uses CQL2 `LIKE` only when `/conformance` advertises it, otherwise the exact `name=` queryable, plus a search of the boundaries already loaded | `/ogc/conformance`, `/items?filter=…` or `?name=` | OGC API – Features Part 3 / CQL2 |
| Area analysis | AOI from a boundary click, search, rectangle, polygon or the current view. Zonal statistics, a per-year series 2001–2011 (chart and table), Level 1 vs Level 2 totals on the same polygon, clip to GeoTIFF | `POST /api/operations/zonal-statistics`, `/clip`, `GET /api/downloads/{job}` | Custom API (not OGC Processes) |
| Pixel readout | Value at a click for A (and B) with units and NoData. A B − A difference appears only when the two grids are comparable | vendor `GET /tiles/datasets/{id}/point/{lon},{lat}` | Vendor-specific |
| Accounts | Sign in and out. Role shown in the top bar. Session expiry banner. 401/403 explained | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Agent 5 API (provisional pin) |
| Admin | Dataset table with version status. Upload (progress) → validate → publish → the layer appears on the map. Provenance per version | `/admin/datasets…`, `/admin/jobs/{id}` | Agent 5 API (provisional pin) |
| Downloads | Full or clipped export, dataset and version, output CRS (list or validated `EPSG:` code), format per dataset kind, with progress, cancel, size and provenance | `/api/exports…`. Without the application API, clipping falls back to `/api/operations/clip` | Agent 5 API (provisional pin) |

**Scientific integrity.** The browser never computes population values. Every total, mean and count
comes from the processing service. The only arithmetic in the UI is the difference or percentage
between two server totals over the same area, and it is labelled that way. Years are never interpolated
in the browser. A display range is never presented as data.

## Configuration: `config.json`

```json
{
  "applicationApi": { "enabled": false, "auth": "/auth", "admin": "/admin", "exports": "/api/exports" },
  "catalogMetadata": null,
  "studyArea": { "name": "India", "bbox": [68.1, 6.5, 97.5, 35.7] },
  "boundaries": { "pageSize": 500, "maxFeaturesPerView": 5000 }
}
```

- `applicationApi.enabled` is `false` on the frozen base because Phase 2 has no `/auth`, `/admin` or
  `/api/exports`. With `false`, the viewer never calls those routes (no 404 noise), shows
  "Accounts not enabled" and clips through the Phase 2 API. **Set it to `true` once Agent 5's service
  is routed by the gateway.**
- `catalogMetadata` is an optional URL returning public provenance (license, group, synthetic flag,
  resolution). See `CHANGE_REQUEST_FOR_AGENT5.md`.
- The initial camera is the study area (India). Only when no published dataset covers at least a
  quarter of it (the Phase 2 fixtures, for example) does the camera fit the published data instead.
  This rule is data-driven: no city is hard-coded.

## Files

| Path | Purpose |
|---|---|
| `index.html`, `style.css` | Page shell and design tokens/components. MapLibre 4.7.1 loads from jsDelivr with SRI; this is the only external request |
| `app.js` | Entry module: route constants, tile URL builders, boot, wiring (`window.map` stays global) |
| `js/catalog.js` | Discovery, normalisation, grouping, provenance (pure; unit-tested) |
| `js/boundaries.js`, `js/vectors.js` | Paginated bbox loading and boundary layers |
| `js/display.js`, `js/legend.js`, `js/rasters.js`, `js/compare.js` | Rasters, legends and shared ranges, timeline, Level 2 overlays, swipe/side-by-side |
| `js/analysis.js`, `js/draw.js`, `js/inspector.js`, `js/search.js` | AOI, operations, pixel readout, place search |
| `js/auth.js`, `js/admin.js`, `js/exports.js` | Application API clients and screens |
| `config.json` | Deployment switches (see above) |
| `docs/` | Spec, provisional Agent 5 OpenAPI pin, change requests, evidence. Not shipped in the image |
| `tests/` | Unit tests, Playwright browser tests and the mock gateway. Not shipped in the image (`.dockerignore`) |

## Tests

```sh
cd demo-client/tests
npm ci                                  # @playwright/test 1.49.1 only
npx playwright install chromium
npm run test:unit                       # node:test, pure logic + static legacy contract
npx playwright test                     # browser tests against two local mock gateways (no Docker)
CGIS_BASE_URL=http://localhost:8080 npx playwright test --project=stack   # read-only checks on a real stack
```

The mock gateway (`tests/mock-gateway/server.mjs`) serves this folder plus test doubles of `/ogc`,
`/tiles`, `/api`, `/auth` and `/admin`. Its data is **MOCK**: 11 national Level 1 years, 6 Level 2 towns,
12,000 boundary cells and the exact Phase 2 synthetic fixtures. Mock dataset ids deliberately differ from
the real naming convention. A green mock run proves the software, not the science.

Agent 7's existing checks still apply unchanged: `tests/e2e/test_60_viewer.py` and
`tests/e2e/browser/check_viewer.py` (ported here as `tests/e2e/legacy.spec.js` and
`tests/unit/legacy-contract.test.js`).
