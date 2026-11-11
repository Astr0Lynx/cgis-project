# Completion handoff report — Agent 04 (PostGIS, OGC services, processing, national-scale platform)

- **Role:** platform and national-scale correctness
- **Branch:** `completion/agent4-platform`
- **Frozen base:** `c4dac59433f7aed90b8223ef6ba00cf4fef6449b` (verified `origin/main` == base before branching)
- **Implementation tip SHA:** see §12 (the commit that adds this report is reported to Agent 7 in the handover
  message; `git log completion/agent4-platform` is authoritative)
- **Completion status:** **PASS for P1, P2, P3 within Agent 4's scope**, with one *required* test update owned
  by Agent 7 (CR-A4-7.1, §8) and the limitations in §10. Real *scientific* Level 1/Level 2 outputs were not on
  this machine; national-scale behaviour was proven with one real India-wide WorldPop raster, real district
  boundaries and a synthetic raster on the exact Level 1 grid (labelled SYNTHETIC everywhere).

## 1. Changed paths (ownership confirmed)

Only Agent 4 paths: `database/`, `ingestion/`, `ogc-services/`, `spatial-processing/`, `infra/`, `compose.yaml`,
`.env.example`, `Makefile`. No edits to `application-api/`, `demo-client/`, `science/`, `tests/e2e/`, `docs/`,
`contracts/`. No PR, no force push, `main` untouched.

## 2. Requirements fulfilled (acceptance matrix rows P1, P2, P3)

| # | Requirement | Implementation | Evidence |
|---|---|---|---|
| 1 | Version-aware admin units | `database/migrations/core/0007_admin_units_versioned` — identity `UNIQUE NULLS NOT DISTINCT (dataset_id, dataset_version, unit_id)`, surrogate PK `unit_key`, `geo.current_admin_units`, `geo.dataset_units()`, `geo.unit_versions()`, ready-version units immutable, spatial primitives read current units, non-destructive rollback. Importer writes per version (`ingestion/catalog.py`). | `database/tests/test_versioned_units.py` (13): upgrade from a 0006 DB with v1 rows (rows byte-identical), reimport v2 keeps v1, current resolution, withdrawal falls back to v1, same `unit_id` in two datasets, rollback refusal/restore. `ingestion/tests/test_import_db.py::test_vector_new_version_keeps_old_version_units` |
| 2 | OGC pagination/scoping | Collections scoped by `dataset_id` AND `dataset_version`; item lookup and prev/next carry the scope (no self-links at the ends); `limit` > 1 000 throttled with `numberMatched` + `next` (never truncated) | `ogc-services/tests/integration/test_versions_and_paging.py` (4: version scoping + withdrawal, cross-collection items/neighbours, 2 500-feature full paging, bbox + paging); national: 12 100-polygon grid and 735 real districts paged completely (§6). CITE unchanged (§5) |
| 3 | Shared raster scale | `core/0008` `catalog.render_profiles` + `catalog.render_profile_for()` (applies only when units are equal); OGC tileset `style` = `viridis linear lo..hi` + "configured shared range from render profile …"; autoscale labelled diagnostic; NoData transparent | `ogc-services/docs/render-profiles.md`; DB tests (3), publisher/adapter unit tests; live: both national rasters render `viridis linear 0..50`, `shared: true` |
| 4 | Raster ingestion audit (India size, CRS, 16 GB) | Block-wise validation already bounded; **added** row-band splitting of oversized source blocks (single-strip GeoTIFFs); Mollweide/non-EPSG sources rejected (`unsupported_crs`, Agent 1 preprocesses); Windows `fsync` fix (Agent 2 CR) | `ingestion/tests/test_raster.py` (+2), `test_manifest_and_paths.py` (+1); national import RSS/time/disk (§6) |
| 5 | Real India-wide vectors, indexes, latency, paging, EXPLAIN | Real geoBoundaries ADM2 (735) imported and paged; synthetic 12 100-polygon grid; EXPLAIN ANALYZE recorded | §6, `infra/docs/evidence/national-bench/national_bench.json` |
| 6 | Large AOI clip/zonal, bounded async jobs | `spatial-processing`: `mode: sync|async|auto`; blockwise zonal/clip (O(block²) memory); PostGIS queue (`core/0009`, `FOR UPDATE SKIP LOCKED`), heartbeat, progress, cancel (`POST /operations/{id}/cancel`), stale-worker retry, queue TTL, time limit, restart-safe; no Redis | `spatial-processing/tests/test_async_blockwise.py` (21 tests / 38 cases) + `test_postgis.py` (+2); national: India-wide zonal 22–28 s, kill-and-recover (§6) |
| 7 | Level 2 grouping without a sparse national raster | per-town datasets grouped by `provenance.group_id` (no schema change, manifest v1 unchanged); `group <id>` fact in OGC descriptions, `group_id` in `/tiles/datasets`; shared `level2-people-per-cell` scale | publisher unit tests, tiler catalog unit tests |
| 8 | Agent 5 gateway/Compose (R1–R6) | all six resolved (`infra/docs/change-requests/agent4-to-agent5-agent6.md`): routes with body limits/streaming, `X-Real-IP`, profile `app` services, `app-grants`, `.env.example` APP_*, `GATEWAY_READ_ACCESS=public|protected`, per-request DNS resolution, admins-only datasets withheld from OGC (fail closed) | `infra/scripts/smoke.sh` (10/10), gateway structured 503 without the app, `ogc-services/tests/integration/test_access_filter.py` |
| 9 | `/viewer/` kept; CITE kept; standard vs non-standard documented | unchanged route; CITE zero failures; `ogc-services/docs/conformance-matrix.md` §6 | §5 |

## 3. Migrations (exact steps)

Forward (idempotent, automatic on `docker compose up`, also `docker compose run --rm migrate`):

| Migration | Old schema | New schema | Rollback |
|---|---|---|---|
| `core/0007_admin_units_versioned` | `geo.admin_units` PK `unit_id` | PK `unit_key bigint identity`; `admin_units_identity` unique; view/functions above; trigger `admin_units_guard` | `migrate down --target 6`: **refuses** while a `unit_id` is stored more than once (no data loss); otherwise restores PK `unit_id` and 0004 functions |
| `core/0008_render_profiles` | — | `catalog.render_profiles` (5 seeded profiles, `ON CONFLICT DO NOTHING`), `catalog.render_profile_for()` | drops table/function |
| `core/0009_job_queue` | `ops.jobs` sync only | + `execution, attempts, max_attempts, progress, progress_message, heartbeat_at, worker_id, cancel_requested`; `running → queued` for async only | refuses while async jobs are queued/running; restores the 0005 guard |

Upgrade of an existing Phase 2 stack: `git pull` → `sh infra/scripts/init-env.sh` (appends the new keys to an
existing `.env`, changes nothing else) → `docker compose --profile tools build` → `docker compose up -d --wait`.
No backfill is needed (the importer always filled `dataset_id/dataset_version`). All new SQL is re-runnable
(`test_migration_sql_itself_is_rerunnable`). No destructive operation is performed or required.

## 4. API routes (public, through the gateway)

| Route | Owner | Change |
|---|---|---|
| `/ogc/…` OGC API Features/Tiles | A4 | version-scoped collections, scoped item links, shared styles, `rel=describedby` |
| `/tiles/…` TiTiler (vendor) | A4 | `/tiles/datasets[/{id}]` add `product_level, resolution, group_id, synthetic, license, citation, render` |
| `/api/operations/clip`, `/api/operations/zonal-statistics` | A4 | optional `mode` (default `sync` = unchanged); 202 + `Location` for async |
| `/api/operations/{id}` | A4 | adds `execution, progress, progress_message, attempts, max_attempts, cancel_requested, links.cancel` |
| `POST /api/operations/{id}/cancel` | A4 | **new** (async jobs only) |
| `/api/ready`, `/api/health` | A4 | `/ready` reports `async` workers; both anonymous in every access mode |
| `/auth/`, `/admin/…`, `/api/exports…`, `/api/catalog/`, `/api/app/` | A5 | routed to `app-api` (profile `app`); `503 APP_API_UNAVAILABLE` without it |
| `/viewer/` | A6 | unchanged |

Not OGC: `/tiles/` (vendor TiTiler), `/api/operations` (custom; **not** OGC API - Processes), the A5 routes.

## 5. Tests (exact commands, numeric results, environment)

Environment: Windows 11, Docker Desktop 28.0.4 (WSL 2 VM: 8 CPUs, 7.7 GiB RAM of a 15.8 GiB host),
PostgreSQL 17 / PostGIS 3.5 image as pinned.

| Command | Result |
|---|---|
| `sh tests/e2e/run_module_suites.sh --cite --qgis` | **MODULE SUITES: PASS** — database 153 passed; ingestion 86 passed; OGC HTTP+unit 227 passed; pygeoapi unit 49 passed; processing test stage (189 passed, 1 skipped) and PostGIS 7 passed; headless QGIS OK; **CITE Features 1.0: 179 total, 173 passed, 0 failed, 6 skipped; CITE Tiles 1.0: 16 total, 15 passed, 0 failed, 1 skipped** (skips: no features in the CITE bbox for 5 checks, free-form query parameters, dataset-tilesets class not implemented — same as Phase 2) |
| `sh tests/e2e/run_e2e.sh --qgis` | all steps pass except `pytest_main`: **101 passed, 1 failed** (`test_processing_api_has_no_catalog_mutation_routes`, the route allow-list — see CR-A4-7.1); persistence **5 passed**; negative imports 6/6 refused; gdalinfo + QGIS GeoTIFF OK; restart OK; secret scan OK; 0 gateway 5xx; startup 100 s. With CR-A4-7.1 applied to a scratch copy: that file 40/40 → 102/102 |
| `sh infra/scripts/smoke.sh` | 10/10 |
| ingestion against the contract stand-in schema | 85 passed, 1 skipped (version test needs 0007) |

The final rerun on the tip commit is recorded in §12.

## 6. National-scale evidence (`sh infra/bench/run_national_bench.sh`; `infra/docs/evidence/national-bench/`)

**Inputs.** REAL (open, CC BY 4.0, not committed): WorldPop Global1 unconstrained `ind_ppp_2011.tif`
(1 849 514 894 B, sha256 `ff6a2b3e…a362ef`) and geoBoundaries gbOpen IND ADM2 commit 9469f09 (735 districts,
sha256 `8bef6929…6db40`) — both **identical to Agent 2's recorded inventory checksums**. SYNTHETIC: a raster on
the exact Level 1 grid (35 075 × 34 497 = 1.21 × 10⁹ cells, 452 195 940 valid; generated in 290 s at 86 MiB RSS)
and a 12 100-polygon grid. Synthetic values are not population data.

**Ingestion** (real CLI: validate every pixel, lossless COG, verify every pixel, register):

| Dataset | Result | Time | Peak RSS (process) | Stored |
|---|---|---|---|---|
| WorldPop India 2011 (real) | imported; sum 1 210 854 976.7, 393 921 894 valid cells — equal to Agent 2's `india-l1-pop-2011` totals (mass-conserving model) | 503 s (validate 57 + COG 338 + verify 108) | **731 MiB** | COG 1.63 GB |
| ADM2 districts (real) | imported, 735 MultiPolygons | 8 s parse/normalise | 491 MiB | 40 MB GeoJSON |
| national-grid (synthetic) | imported, sums verified | within 2 036 s total run | — | COG 107 MB |

`docker stats` peak of the ingestion container during the run: 2.51 GiB (it counts active page cache from
re-reading the 1.85 GB source); the default limit is now 4 GiB. Asset volume: 1.73 GB for both rasters + vectors.
Free-disk rule: ~2× the largest source while importing (intermediate GTiff + COG in staging).

**OGC / tiles** (gateway, median of 3): PNG tiles z3–z11 over Delhi 25–57 ms (WorldPop), 26–39 ms (synthetic);
vendor tile 28–48 ms. Both rasters: `viridis linear 0..50`, shared profile (`level1-people-per-cell`,
`worldpop-people-per-cell`).

**Features:** 12 100-polygon grid: `numberMatched` 12 100, 13 pages of 1 000, all unique, complete; page median
25 ms (max 441 ms, first page after publication); bbox query 27 ms; item 8 ms. Real ADM2: 735/735 in 8 pages of
100, median 39 ms; item 10 ms. EXPLAIN ANALYZE: GiST used for bbox (1.9 ms) and `units_in_bbox` in EPSG:3857
(3.5 ms); the collection page/count use a sequential scan because the scope selects 94 % of the table
(5.7 ms / 1.9 ms) — with selective scopes the `admin_units_identity` index is used (`database` test at 14 400 rows).

**Processing** (WorldPop | synthetic):

| Case | Result |
|---|---|
| district AOI 0.35° × 0.45°, sync | 96 ms \| 208 ms; async identical sum and count (`matches_sync: true`) |
| state AOI 7.6° × 6.6° (72 M px), sync | `413 AOI_TOO_LARGE` + hint to use async |
| same, async/auto | 202 → completed in 4.1 s \| 3.1 s, 20 blocks |
| all-India rectangle (1.21 × 10⁹ px), async | 27.5 s \| 22.4 s, 306 blocks; synthetic sum and count **exactly equal** to the generator's; WorldPop 393 918 707 cells (3 187 edge cells lie outside the requested rectangle) |
| clip 1° × 0.8° as COG, async | 1 s; 1200 × 960, EPSG:4326, NoData −99999, native grid (no resampling); downloaded and opened in headless QGIS (both OK) |
| cancel a running India job | stopped within 2 s, `CANCELLED` |
| kill the processing container during an India job | job re-queued, completed on attempt 2/2 with 452 195 940 valid cells |

**Memory (docker stats peaks during the whole benchmark):** db 143 MiB, ogc 290 MiB, processing 265 MiB,
tiles 205 MiB, gateway 12 MiB, viewer 9 MiB, ingestion 2.5 GiB (incl. page cache; process RSS 731 MiB). The
platform's limits total ~6.7 GiB (+4 GiB during an import, +4.3 GiB with `app`), inside the 16 GB target.

**Capacity limits (published):** sync AOI ≤ 25 M pixels (`MAX_WINDOW_PIXELS`); async AOI ≤ 2 × 10⁹ pixels,
clip ≤ 2 GiB uncompressed, ≤ 3 600 s, 2 attempts, queue TTL 6 h, 1 worker thread (`PROCESSING_ASYNC_*`);
4 concurrent sync jobs; OGC pages ≤ 1 000 features.

## 7. Cross-agent requests resolved

* **Agent 5 R1–R6:** all resolved (table in `infra/docs/change-requests/agent4-to-agent5-agent6.md`).
* **Agent 6 1–6:** shared range (1), gateway (2), public metadata via `/tiles/datasets` + `describedby` (3),
  `numberMatched`/`next` (5), TiTiler routes (6) done; CQL2 `LIKE` (4) **not declared** (needs CITE first).
* **Agent 3 C3 grouping:** `provenance.group_id` exposed; per-town IDs and URLs unchanged.
* **Agent 2:** `science` profile `level1` service; national import facts (§6); Windows `fsync` defect fixed;
  shared Level 1 legend.

## 8. Requests to Agent 7 (`infra/docs/change-requests/agent4-to-agent7-e2e-and-contracts.md`)

* **CR-A4-7.1 (required):** add `/operations/{job_id}/cancel` (POST) to the allow-list in
  `tests/e2e/test_50_negative.py::test_processing_api_has_no_catalog_mutation_routes` (diff provided, verified).
* **CR-A4-7.2:** record the `geo.admin_units` identity change as a versioned contract decision (D-A4-1).
* **CR-A4-7.3:** set `COMPOSE_PROFILES=app` on the integrated branch; decide D-A5-1 (`GATEWAY_READ_ACCESS`).

## 9. Real versus synthetic

Real: WorldPop India 2011 source raster and geoBoundaries ADM2 — imported, tiled, paged, clipped, aggregated.
Synthetic: the national-grid raster, the 12 100-polygon grid, all unit/integration fixtures. **No scientific
Level 1/Level 2 output was produced or claimed by Agent 4**; Agent 2's 11 COGs (13.2 GB) live on Agent 2's machine
and must be imported by Agent 7 with Agent 2's manifest (`docker compose run --rm ingestion import --manifest …`).

## 10. Limitations and risks

* Pixel-centre ties: when an AOI edge passes *exactly* through a pixel centre, the in-memory and blockwise
  paths may include/exclude that one boundary pixel differently (measure-zero; documented in `docs/API.md`).
* Large clips reprojected to another CRS need a raster NoData value (blockwise path), else `422`.
* Async workers live inside the processing container (1 thread by default); horizontal scaling = more replicas
  sharing the queue (safe by design, not load-tested).
* Measurements are on Docker Desktop with a 7.7 GiB VM on a 15.8 GiB host, not on a Linux 16 GB host.
* CQL2 `LIKE` not declared; OGC API - Tiles `datetime` and dataset-level tilesets not implemented (as Phase 2).
* `GATEWAY_READ_ACCESS=protected` was not exercised end-to-end here (needs `application-api/`); Agent 5's own
  gateway test covers the `auth_request` configuration that this branch reproduces.
* The admins-only OGC filter fails closed: if `cgis_app.dataset_access` exists but is unreadable, publication
  stops refreshing (logged) instead of publishing restricted datasets.

## 11. Runbook additions

`infra/README.md` (profiles, memory budget, gateway modes, troubleshooting), `database/README.md` (identity,
views, rollback), `spatial-processing/docs/API.md` (async jobs), `ogc-services/docs/render-profiles.md`,
`make bench` / `sh infra/bench/run_national_bench.sh` (separate project `cgis-bench`; tear down with
`docker compose -p cgis-bench down -v`).

## 12. Final verification on the tip commit

Run on commit `56cb68a` (all code of this branch; the following commit only adds this section and the
evidence files). Evidence: `infra/docs/evidence/final-run/`.

| Command | Result |
|---|---|
| `sh tests/e2e/run_module_suites.sh --cite --qgis` | **PASS**: database 153; ingestion 88; OGC HTTP+unit 228 (incl. admins-only filter and version/paging tests); pygeoapi unit 49; processing test stage + PostGIS 7; QGIS OK; CITE Features 173/179 passed, **0 failed**, 6 skipped; CITE Tiles 15/16 passed, **0 failed**, 1 skipped |
| `sh tests/e2e/run_e2e.sh --qgis` | main **101 passed, 1 failed** (CR-A4-7.1 allow-list only); persistence **5 passed**; every other step PASS (seed ×2 idempotent, 6 negative imports refused, gdalinfo, QGIS GeoTIFF, restart, no secret leaks, 0 server errors) |
