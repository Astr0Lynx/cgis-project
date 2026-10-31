# CyberGIS Phase 2: final integration report

| | |
|---|---|
| **Status** | **All P0 gates passed.** `main` was fast-forwarded to the report commit (§11). |
| Integration branch | `phase2/integration` |
| Tested code commit | `d4b6cfc61b461009e380708a54152194b4b9fb97`. Fresh clone from GitHub, no-cache image build, every gate below. |
| Report commit | the commit that adds this file and `evidence/` (documentation only on top of the tested commit) |
| Default branch | `main`: fast-forwarded from `264bce0` (the BASE_SHA parent, unchanged since the baseline) to the report commit |
| Data | **SYNTHETIC deterministic fixtures only.** Not real population data, not WorldPop, not a scientific estimate. |
| Date / machine | 2026-10-31 · Windows 11, Docker Desktop (Engine 29.1.3, Compose v5.0.0), 32 CPUs / 16 GB RAM for Docker, Git Bash |

Related documents: [`RUNBOOK.md`](RUNBOOK.md) (how to run and operate it),
[`INTEGRATION_LOG.md`](INTEGRATION_LOG.md) (merge record, every fix, contract decisions) and
[`evidence/`](evidence/) (raw outputs quoted below).

## 1. Summary

The five feature branches were merged in order (Agent 1 → 5) with no textual conflicts. Running the
merged system exposed **nine integration defects** (F1–F9 in `INTEGRATION_LOG.md`):

- two start-up crashes (F1, F2);
- an invalid OpenAPI document that also stopped QGIS from loading the boundaries layer (F3);
- a redirect QGIS would not follow (F4);
- a role/schema name collision (F5);
- a missing Phase 1 import mount (F6);
- a tool image that `up --build` never rebuilt (F7);
- a viewer zoom bug (F8);
- a restart race (F9).

Each was fixed with the smallest change and recorded with its owner. Three module test suites
(Agents 3, 4 and 5) needed six setup-only changes (T1–T6), because they had been written against
looser stand-in schemas.

After those fixes, the following all pass from a fresh clone:

- the end-to-end suite;
- every module owner's suite, now run against the real integrated stack (which none of the agents
  could do on their own);
- the OGC CITE Features and Tiles suites;
- headless QGIS checks;
- a headless-browser run of the diagnostic viewer.

### P0 acceptance matrix (`contracts/ACCEPTANCE_MATRIX.md`)

| # | P0 requirement | Result | Evidence |
|---|---|---|---|
| 1 | Fresh clone builds and runs via documented Compose commands | **PASS** | Fresh clone of `d4b6cfc`, `.env` from `init-env.sh` only (plus a port change), no-cache build, `up --wait` in 32 s. `evidence/build-nocache.txt`, `compose-health.txt` |
| 2 | Extensions, migrations, indexes and persistent volumes initialise deterministically | **PASS** | `migrate` applies 0001–0006 and the roles, idempotently; GiST indexes checked (`test_gist_indexes_exist`); persistence after `down`/`up` and after a bare `restart` |
| 3 | Generator creates both rasters, the towns and manifests with synthetic provenance | **PASS** | `seed-run1.jsonl`; `test_generated_fixtures_are_labelled_synthetic`; COG tags `SYNTHETIC=TRUE` |
| 4 | Ingest twice is idempotent; bad CRS/file, conflicts and invalid geometry fail clearly without corrupting ready data | **PASS** | `seed-run2.jsonl` (3 × unchanged); `evidence/negative-imports/*` (6 × exit 1 with an error code); `test_bad_imports_did_not_corrupt_ready_datasets` |
| 5 | `catalog.datasets` has the correct year, CRS, bbox, NoData, units, provenance and paths; `geo.admin_units` supports spatial filtering | **PASS** | `test_raster_catalog_metadata`, `test_vector_catalog_metadata`, `test_admin_units_*`, `test_spatial_filter_on_admin_units`; query plans in §5 |
| 6 | OGC Features landing, conformance, collections and items serve Town A/B; bbox works; response CRS per the standard | **PASS** | `test_20_ogc_features.py` (16 tests); CITE Features 0 failures; QGIS OAPIF 10/10 |
| 7 | Tiles render without arbitrary filesystem or remote access; OGC API Tiles discovery, TMS and tiles verified; vendor routes identified | **PASS** | `test_30_tiles.py`; CITE Tiles 0 failures; unsafe-asset refusals (e2e + Agent 4 T2); §9 conformance table |
| 8 | Town A 2001 = 100, 2011 = 200; Town B 2001 = 25, 2011 = 50 (pixel centre) | **PASS** | `test_zonal_known_values` ×4, the browser check, §7 |
| 9 | Clip/export gives a correctly georeferenced GeoTIFF, downloadable; non-overlap and invalid AOI fail explicitly | **PASS** | `test_clip_*`; `gdalinfo-clip.txt`; `qgis-geotiff.txt`; `test_out_of_bounds_clip`, `test_malformed_aoi_*` |
| 10 | Health checks, logs, API docs, safe errors, reproducible test commands | **PASS** | Readiness health checks on all services; JSON gateway log with 0 × 5xx; `/ogc/openapi`, `/api/docs`; contract error shape asserted; `run_e2e.sh`/`run_module_suites.sh` |
| 11 | `phase2/integration` includes all five branches; e2e passes from a fresh checkout; no secrets or synthetic-as-real in history | **PASS** | §11 graph (5 `--no-ff` merges); fresh-clone run; the secret scan covers logs, evidence and tracked files; every fixture title and provenance entry says SYNTHETIC |

## 2. Architecture (as deployed)

```
            host :8080 (127.0.0.1)
                  │
              gateway (nginx 1.27) ── JSON access log, GET-only on read prefixes
   ┌──────────┬───┴──────┬───────────────┬──────────┐
 /viewer/   /ogc/      /tiles/          /api/
 viewer     ogc        tiles            processing
 (static)   pygeoapi   TiTiler          FastAPI
            0.24.0     (restricted)     clip / zonal / jobs / downloads
              │          │  ▲ ro            │  ▲ ro     │ rw
              │          │  └── assets ─────┘  │       processing-outputs
              └────┬─────┘        ▲ rw         │
                   ▼              │            ▼
                  db (PostgreSQL 17 + PostGIS 3.5) ── pgdata
                   ▲      catalog.datasets / current_datasets, geo.admin_units, ops.jobs
                   │
   migrate (one-shot: cgis_db migrations + per-service login roles)
   ingestion (tool: fixture generator + importer; the only writer of assets)
```

- **Rasters** are stored as COG files on the `assets` volume, written only by ingestion.
  **Vectors, the catalog and jobs** live in PostGIS. Every service resolves a dataset through
  `catalog.current_datasets`, and no service takes a file path from a client.
- **Least-privilege logins.** `ingestion` runs as a `cgis_ingest` member. `ogc` and `tiles` run as
  a `cgis_reader` member with read-only sessions. `processing` runs as a `cgis_processing` member.
  Only `db` and `migrate` use the owner role.
- **Start order.** The database is reachable only inside the Compose network. Services start in
  this order: `volume-init`, `db` healthy, `migrate` completed, `tiles` healthy, then `ogc`,
  `processing` and `viewer` healthy, then `gateway`. Each health check tests readiness (DB plus
  volume), not just that a process is up.

## 3. Service URLs

| URL (default `http://localhost:8080`) | Interface | Standard? |
|---|---|---|
| `/viewer/` | Diagnostic MapLibre viewer | — |
| `/ogc/`, `/ogc/conformance`, `/ogc/openapi` | OGC API landing page, conformance, OpenAPI 3.0 | OGC API - Common |
| `/ogc/collections[/{id}]` | One collection per ready catalog dataset | OGC API - Features / Common |
| `/ogc/collections/synthetic-towns/items[/{id}]` | Town A / Town B; bbox, paging, `crs`, `bbox-crs` | OGC API - Features Parts 1–2 |
| `/ogc/collections/synthetic-worldpop-{2001,2011}/tiles/...` | Map tilesets, metadata and PNG tiles | OGC API - Tiles 1.0 |
| `/ogc/TileMatrixSets[/{id}]` | WebMercatorQuad, WorldCRS84Quad | OGC TMS 2.0 |
| `/tiles/datasets/...` | TileJSON, `{z}/{x}/{y}.png`, `/info`, `/point` | **Vendor-specific (TiTiler), not OGC** |
| `/api/operations/clip`, `/api/operations/zonal-statistics`, `/api/operations/{job_id}`, `/api/downloads/{job_id}`, `/api/docs` | Processing | **Custom API, not OGC API - Processes** |

## 4. Exact run commands

```sh
git clone https://github.com/Astr0Lynx/cgis-project.git && cd cgis-project
sh infra/scripts/init-env.sh                 # .env from .env.example with random passwords
docker compose --profile tools build         # all images (pinned bases)
docker compose up -d --wait
docker compose run --rm ingestion seed       # SYNTHETIC fixtures -> COG assets + catalog + admin_units
sh infra/scripts/smoke.sh
# acceptance
sh tests/e2e/run_e2e.sh --browser --qgis
sh tests/e2e/run_module_suites.sh --cite --qgis
```

On the integration machine port 8080 was taken by a local GeoServer, so the test stacks used
`GATEWAY_PORT=8088/8089` with a matching `PUBLIC_BASE_URL`. That is the only `.env` change made,
and it is documented in `.env.example`.

## 5. Tests and results (tested commit `d4b6cfc61b461009e380708a54152194b4b9fb97`, fresh clone)

| Gate | Command | Result |
|---|---|---|
| No-cache image build | `docker compose --profile tools build --no-cache --pull` | exit 0, 1 min 9 s |
| Start-up to all healthy | `docker compose up -d --wait` | exit 0, 32 s; 6 long-running services healthy, 2 one-shots exited 0 |
| Seed ×2 | `docker compose run --rm ingestion seed` | run 1: 3 × `imported`; run 2: 3 × `unchanged` |
| Negative imports | the real CLI, 6 bad inputs | all exit 1 with `corrupt_raster`, `missing_crs`, `unsupported_crs`, `checksum_conflict`, `invalid_geometry`, `file_not_found`; ready rows untouched |
| E2E pytest | `run_e2e.sh` (in-network) | **102 passed, 0 failed** |
| GeoTIFF check | `gdalinfo -stats` (host GDAL) | GTiff 10×10, EPSG:4326, origin (78.01, 17.99), pixel 0.001, NoData −9999, min = max = mean = 1, `Unit Type: people_per_cell` |
| QGIS opens the export | `tests/e2e/qgis/check_geotiff_qgis.py` (QGIS 3.44.15) | **6/6**: CRS, 10×10, extent, NoData, sum 100 |
| Viewer in a browser | `tests/e2e/browser/check_viewer.py` (Chromium, Playwright 1.49) | **12/12**: tiles loaded, Town A click → zonal 100 (2001) / 200 (2011), clip download, vendor switch, 0 console errors, 0 failed requests |
| Persistence | `down` (volumes kept) → `up --wait` → pytest `-m persistence` | **5 passed**: catalog, features, tiles, zonal 100, earlier export byte-identical |
| Log scan | `Traceback`/`CRITICAL`/`FATAL`/`[emerg]` in the service logs, counted after the restart | 0 in every service. Before the restart, pygeoapi logs each deliberate 4xx from the negative tests with a traceback, and PostgreSQL logs FATAL "terminating connection" during the restart; neither is a server fault, so 5xx responses are the gate. |
| Bare `docker compose restart` | then `up --wait` + smoke | exit 0, 8/8 routes |
| Secrets | `.env` passwords searched in logs, evidence and tracked files | 0 found |
| Server errors | 5xx responses in the gateway access log during the whole run | **0** |
| Agent 2 suite | pytest against PG 17 / PostGIS 3.5 | **136 passed** |
| Agent 3 suite | pytest, `INGESTION_TEST_SCHEMA=existing`, DB migrated with Agent 2's migrations | **84 passed** |
| Agent 4 suites | unit + HTTP integration through the integrated gateway; pygeoapi adapter unit tests | **215 passed**; **47 passed** |
| Agent 5 suites | Docker `test` stage; `pytest -m postgis` against the stack | **151 passed** (1 PostGIS test skipped there); **5 passed** |
| OGC CITE Features | `ets-ogcapi-features10` 1.8, TEAM Engine 5.7 | **179 tests: 173 passed, 0 failed, 6 skipped** |
| OGC CITE Tiles | `ets-ogcapi-tiles10` 1.1, TEAM Engine 5.7 | **16 tests: 15 passed, 0 failed, 1 skipped** |
| QGIS OAPIF + XYZ | Agent 4's `tests/qgis/check_qgis.py` (QGIS 3.44.15) | **10/10** |
| Gateway routing (mock upstreams) | `infra/tests/test_gateway_routes.sh` | **19/19** (run on the integration worktree after the last gateway change) |

CITE skips:

- **Features:** five skips come from the ETS's fixed bounding boxes, which return no features
  outside the fixture area. One skip is for free-form query parameters, which the server allows.
- **Tiles:** the one skip is the `dataset-tilesets` class, which is not claimed.

Raw outputs are in [`evidence/`](evidence/).

**Performance** (informational, 64×64 fixture, 15 runs after warm-up, in-network client):

| Request | median | p95 | max |
|---|---:|---:|---:|
| OGC items (2 features) | 6.2 ms | 8.1 ms | 9.2 ms |
| OGC items with bbox | 19.2 ms | 19.8 ms | 20.3 ms |
| OGC API Tiles PNG (z13) | 13.3 ms | 15.3 ms | 34.1 ms |
| TiTiler PNG (same tile) | 9.7 ms | 13.0 ms | 14.6 ms |
| Zonal statistics, Town A | 32.5 ms | 35.6 ms | 37.0 ms |
| Clip Town A → GeoTIFF job | 37.5 ms | 42.9 ms | 44.0 ms |

These show interactive response times on a laptop-class machine. They are not a benchmark for
real Phase 1 rasters.

**Query plans.** On the integrated database the planner uses `admin_units_geom_gist` for
`geom && envelope AND ST_Intersects(...)` and `datasets_bbox_gist` for catalog bbox queries.
Agent 2's `database/docs/EXPLAIN_ANALYZE.md` has the 22.5k-unit benchmark: GiST plans throughout,
and a sequential scan only when the indexed column itself is transformed. No speed-up is claimed
for the 2-row fixture.

## 6. Endpoint samples (from the integrated stack)

```text
GET /ogc/collections/synthetic-towns/items?f=json&bbox=78.009,17.979,78.021,17.991
200  Content-Type: application/geo+json   Content-Crs: <http://www.opengis.net/def/crs/OGC/1.3/CRS84>
{"type":"FeatureCollection","numberMatched":1,"numberReturned":1,
 "features":[{"id":"town-a","properties":{"unit_id":"town-a","name":"Town A","level":"synthetic_town"}, ...}]}

GET /ogc/collections/synthetic-worldpop-2001/tiles/WebMercatorQuad/13/3680/5871?f=png
200  Content-Type: image/png          (tileMatrixSetLimits advertise z0-13 for this fixture)

GET /tiles/datasets/synthetic-worldpop-2011/point/78.015,17.985          (vendor-specific)
200  {"coordinates":[78.015,17.985],"values":[2.0],"band_descriptions":["SYNTHETIC people_per_cell (2011 label)"]}

POST /api/operations/zonal-statistics  {"dataset_id":"synthetic-worldpop-2011","aoi":<Town B>,"aoi_crs":"EPSG:4326"}
200  {"dataset_id":"synthetic-worldpop-2011","dataset_version":1,"count_valid":25,"sum":50.0,"mean":2.0,
      "units":"people_per_cell","sum_semantics":"total_count","pixels_in_aoi":25,"nodata_excluded":0,
      "aoi_crs":"EPSG:4326","raster_crs":"EPSG:4326",
      "pixel_selection":{"rule":"pixel_center","all_touched":false, ...}, "job_id":"…","status":"completed"}

POST /api/operations/zonal-statistics  {... AOI 79.00-79.01 E, 19.00-19.01 N ...}
422  {"error":{"code":"AOI_OUTSIDE_RASTER","message":"the AOI does not overlap the dataset's raster extent", ...}}

POST /api/operations/clip  {"dataset_id":"synthetic-worldpop-2001","aoi":<Town A>,"aoi_crs":"EPSG:4326","format":"GTiff"}
200  {"job_id":"…","kind":"clip","status":"completed","links":{"download":"/api/downloads/…"}, ...}
GET /api/downloads/{job_id}
200  Content-Type: image/tiff; application=geotiff
     Content-Disposition: attachment; filename="clip_synthetic-worldpop-2001_v1_<12 hex>.tif"
```

## 7. Fixture known values (verified through the public API)

Pixel-centre inclusion (`all_touched=false`), native grid, no resampling:

| AOI | Valid pixels | 2001 sum | 2011 sum | 2001 mean | 2011 mean | NoData excluded |
|---|---:|---:|---:|---:|---:|---:|
| Town A (78.010–78.020 E, 17.980–17.990 N) | 100 | **100** | **200** | 1 | 2 | 0 |
| Town B (78.030–78.035 E, 17.950–17.955 N) | 25 | **25** | **50** | 1 | 2 | 0 |
| NoData-overlap AOI (78.000–78.010 E, 17.990–18.000 N) | 36 of 100 | 36 | 72 | 1 | 2 | 64 |
| Disjoint AOI (79.00–79.01 E) | — | `422 AOI_OUTSIDE_RASTER` | | | | |

Other checks that give the same numbers:

- the Town A AOI given in EPSG:3857 (sum 100);
- the Town A geometry exactly as served by OGC API Features (100 / 200);
- the exported clip read with rasterio, `gdalinfo` and QGIS (sum 100);
- Agent 2's optional PostGIS Raster track (`ST_Clip` + `ST_SummaryStatsAgg`), which returns
  100/200/25/50 in its tests on the integrated database.

## 8. QGIS verification status

| What | How | Status |
|---|---|---|
| OGC API - Features layer (`url='…/ogc' typename='synthetic-towns'`), CRS, geometry types, extent, spatial filter | Headless QGIS 3.44.15 in Docker (`qgis/qgis` pinned digest), Agent 4's script | **Verified 10/10** on the integrated stack |
| OGC API - Tiles as an XYZ layer: valid-cell colour, transparent NoData, NoData boundary at 78.004 E | same | **Verified** (included in the 10/10) |
| Exported clip GeoTIFF: CRS, size, extent, NoData, statistics | Headless QGIS 3.44.15, `tests/e2e/qgis/check_geotiff_qgis.py` | **Verified 6/6** |
| Desktop QGIS GUI | — | **Not exercised in this session.** The host has an OSGeo4W installation, but no GUI session was used. Manual procedure: `ogc-services/docs/usage.md` §4 (Data Source Manager → WFS / OGC API - Features → `http://localhost:8080/ogc`; XYZ tiles with the OGC template), and Layer → Add Raster Layer for the downloaded GeoTIFF. |

## 9. OGC conformance (what is claimed vs. what is verified)

| Class / interface | Declared in `/conformance` | Verification |
|---|---|---|
| OGC API - Common core, landing page, JSON, HTML, OAS 3.0 | yes | CITE Features ETS (shared Common tests); e2e |
| Features Part 1: Core, GeoJSON, HTML, OAS 3.0 | yes | **CITE `ets-ogcapi-features10` 1.8: 0 failures**; e2e |
| Features Part 2: CRS by reference (`crs`, `bbox-crs`, `Content-Crs`) | yes | CITE (Part 2 tests in the same ETS); e2e lat/lon axis-order test |
| Features Part 3: queryables, queryables-as-query-parameters | yes | Agent 4's repository tests only (no CITE ETS used) |
| CQL2 Basic, CQL2 text | yes (subset) | Agent 4's repository tests only. Only a subset of Basic CQL2 operators is implemented. |
| Tiles 1.0: Core, tileset, tilesets-list, geodata-tilesets | yes | **CITE `ets-ogcapi-tiles10` 1.1: 0 failures**; e2e |
| Tiles 1.0: PNG, OAS 3.0 | yes | e2e pixel tests and Agent 4's tests (the ETS has no PNG class) |
| Tiles 1.0: dataset-tilesets | **no** | not claimed (the CITE skip) |
| OGC API - Coverages | **no** | **not implemented**. It is a draft specification, and nothing claims it. |
| OGC API - Processes | **no** | Not implemented. pygeoapi links an empty `/processes` page (no class declared). `/api/` is a custom API. |
| `/tiles/` TiTiler routes | n/a | **Vendor-specific.** Restricted to catalogued ready assets; not presented as OGC API - Tiles. |

## 10. Known limitations

- **Synthetic data only.** The real WorldPop/GHSL/LULC pipeline belongs to Phase 3.
- **Owner reviews pending.** The integration fixes F1–F9 and test changes T1–T6 changed other
  owners' files. They are recorded, but the owners have not reviewed them yet (see
  `INTEGRATION_LOG.md` §5).
- **Feature properties.** Only `unit_id`, `name` and `level` are published, controlled by
  `OGC_VECTOR_PROPERTIES`. The source attributes kept in `geo.admin_units.properties` (JSONB) are
  not exposed through OGC yet. Phase 4 can publish selected attributes as real columns or flatten
  them in a view.
- **Unit identity.** `geo.admin_units.unit_id` is global, and importing a new version of a boundary
  set replaces its units in place. Proposal B1 (version-aware units) is deferred.
- **Tile zoom limits.** OGC tilesets advertise zoom levels up to the fixture's native resolution
  (z13), and deeper requests return 404 as the standard requires. TiTiler's TileJSON advertises
  only z10. Clients must over-zoom.
- **Synchronous processing.** Jobs keep a stable job and status contract, but results live in
  `result.json` on the outputs volume (default TTL 24 h). `ops.jobs.result` exists and is unused.
- **No RBAC.** There is no authentication. The only write path is the ingestion CLI inside the
  Compose network, while the processing POSTs create jobs and outputs only. Full RBAC is Phase 4.
- **External dependencies.** The viewer loads MapLibre from jsDelivr (pinned with SRI). Images are
  pulled from Docker Hub, MCR and GHCR (pinned digests).
- **Restarts.** nginx resolves upstream names at start-up. After recreating a single backend
  container, restart the gateway.
- **Desktop QGIS.** The GUI was not exercised (§8).
- **Agent 1 provenance.** The infrastructure branch was completed by this integration session at
  the operator's request (`INTEGRATION_LOG.md` §1).

## 11. Git record

```text
* (report commit: docs/integration/FINAL_REPORT.md + evidence/)
* d4b6cfc e2e: optional headless-QGIS check of the exported clip GeoTIFF
* c1d55ea migrate waits for the database; restart via down + up
* 46511a8 Agent 6: diagnostic viewer, end-to-end suite, module-suite runner, runbook
* 7d1715c Integration fixes after merging the five Phase 2 branches
*   4436de5 Integrate phase2/agent5-processing (315e503) into phase2/integration
|\  
| * 315e503 Agent 5: record implementation commit SHA in HANDOFF_REPORT
| * 2340753 Agent 5: spatial processing service (clip, zonal stats, export, jobs)
* |   694fd27 Integrate phase2/agent4-ogc (1f13e4f) into phase2/integration
|\ \  
| * | 1f13e4f Agent 4: handoff report for phase2/agent4-ogc
| * | 6e64e00 Agent 4: OGC API Features/Tiles publication and restricted raster tiles
| |/  
* |   973e0f5 Integrate phase2/agent3-ingestion (35f16ef) into phase2/integration
|\ \  
| * | 35f16ef Agent 3: add HANDOFF_REPORT.md
| * | 068fca5 Agent 3: ingestion pipeline, COG conversion, catalog registration, SYNTHETIC fixtures
| |/  
* |   bf2b38f Integrate phase2/agent2-postgis (98003ef) into phase2/integration
|\ \  
| * | 98003ef Agent 2: handoff report
| * | e4135ee Agent 2: PostGIS schema, migrations, spatial SQL primitives and tests
| |/  
* |   2854d38 Integrate phase2/agent1-infrastructure (201c575) into phase2/integration
|\ \  
| |/  
|/|   
| * 201c575 Agent 1: Docker Compose stack, gateway, environment and task commands
|/  
* cc395c6 Phase 2 baseline: frozen v1 contracts and module skeletons
```

| Ref | SHA |
|---|---|
| `main` before publication | `264bce05ab149851d750b56cdf0e0275c60d33be` |
| `phase2/base` (BASE_SHA) | `cc395c69ee738b4597684cf2ccfb84254c15723c` |
| `phase2/agent1-infrastructure` | `201c575e3aaf8ee622c2e4aef83738151fda2502` |
| `phase2/agent2-postgis` | `98003efdf50f4aa5021944629bf33aaea57dd561` |
| `phase2/agent3-ingestion` | `35f16efa86ee995d9e1404b30d1fd1121d901d0f` |
| `phase2/agent4-ogc` | `1f13e4f9e92d5beafc80d6024f033e34830eac09` |
| `phase2/agent5-processing` | `315e503f4a0486acd354bb4b51b29438a440df32` |
| tested code commit | `d4b6cfc61b461009e380708a54152194b4b9fb97` |

All merges are `--no-ff`. Nothing was force-pushed, rebased, deleted or opened as a PR.
`main` was updated with a plain, non-force `git push origin phase2/integration:main`. Git accepts that only as a fast-forward, because `264bce0` is an ancestor of the integration branch.

## 12. Phase 3 / Phase 4 integration contract

**Phase 3 (scientific pipeline).** It delivers outputs as files plus a manifest, and needs no
backend change:

- **Manifest.** Use the same manifest as Phase 1 (`ingestion/docs/PHASE1_HANDOFF.md`). Set
  `product_level: level1` (≈100 m) or `level2` (≈23.5 m town refinement), the reference `year`,
  and explicit `units` (`people_per_cell` for counts; a density product must say so, for example
  `people_per_km2`). Include `checksum_sha256`, `expect`, and `provenance` with the model commit,
  inputs and method.
- **Import.**
  `docker compose run --rm ingestion validate|import --manifest /data/incoming/<delivery>/manifest.json`.
  The importer reads CRS, bounds, NoData and resolution from the file, so a projected CRS such as
  UTM 44N is fine. It converts to a lossless COG, verifies the round trip, and only then marks the
  dataset `ready`.
- **Versions.** A changed product is imported as a new `version`. `catalog.current_datasets` then
  serves the highest ready version, and older versions are kept.
- **Automatic availability.** A new dataset appears in `/ogc/collections` and the tiles routes
  within `OGC_REFRESH_INTERVAL` seconds. `/api/operations/*` accept it by `dataset_id`. Nothing in
  any service is keyed on the synthetic ids, years or product levels.
- **Comparisons.** Level 1 vs Level 2, or comparison against census/GHSL, are Phase 3 analyses.
  They can use the zonal statistics API (pixel-centre rule, `units`-aware sums) or read the COGs
  directly.

**Phase 4 (application, RBAC).** It consumes these interfaces unchanged and adds identity and UX
around them:

- **OGC API - Features** (`/ogc/collections/{id}/items`): boundaries.
- **OGC API - Tiles** (`/ogc/collections/{id}/tiles/...`): use the advertised
  `tileMatrixSetLimits` and over-zoom beyond them.
- **Custom processing API** (`/api/...`): clip, zonal statistics, job status and downloads. Its
  schemas are in `/api/openapi.json`.
- **Vendor TiTiler** (`/tiles/...`): only where its extra features (rescale, colormaps, `/point`)
  are needed. It is not an OGC interface.
- **Authentication.** Add it at the gateway or in a Phase 4 service in front of it. The core
  raster storage (COG volume plus catalog) and the frozen v1 tables stay as they are, and
  per-service DB roles already separate reading from ingesting.
