# HANDOFF REPORT — Agent 5 (Spatial processing)

## Branch / commit status

| Item | Value |
|---|---|
| Base | `phase2/base` @ `cc395c69ee738b4597684cf2ccfb84254c15723c` (verified equal to `origin/phase2/base`) |
| Feature branch | `origin/phase2/agent5-processing` (created at BASE_SHA, pushed) |
| Implementation commit | `IMPL_SHA_PLACEHOLDER` (all code, tests, docs) |
| Branch HEAD | the commit after it, which only records the SHA above in this report; verify with `git rev-parse origin/phase2/agent5-processing` |
| PR / merges | none (per policy) |

Files changed are confined to `spatial-processing/`; contracts, other modules and root files are untouched.
The original `spatial-processing/README.md` placeholder was replaced, which the baseline allows.

## Delivered

* FastAPI service (`src/spatial_processing/`) with
  `POST /operations/clip`, `POST /operations/zonal-statistics`, `GET /operations/{job_id}`,
  `GET /downloads/{job_id}`, plus `GET /downloads/{job_id}/metadata`, `GET /health` and `GET /ready`.
  Served under `/api/` by the gateway, with OpenAPI at `/api/openapi.json` and a snapshot in `docs/openapi.json`.
* Catalog-only asset resolution via `catalog.current_datasets` (highest ready version). Not-ready → 409,
  unknown → 404, vector → 422. Paths are confined to `ASSET_ROOT`: no URLs, `/vsi*`, `..` or symlink
  escape, GeoTIFF driver only.
* AOI validation: GeoJSON Polygon/MultiPolygon/Feature, explicit `aoi_crs` (`EPSG:<code>` 2D only),
  finite numbers, closed rings, OGC validity, non-zero area, lon/lat range, vertex/part limits.
  The AOI is transformed (densified) to the raster's native CRS.
* Pixel-center rule (`all_touched=false`) for all analytics. NoData, masks, catalog NoData and NaN are
  excluded and reported (`nodata_excluded`); results are never coerced to 0.
* Zonal `sum`/`mean`/`count_valid` with explicit `units` and `sum_semantics`. Density units have `sum`
  omitted or rejected (`422 UNIT_SEMANTICS`); unknown units get a qualified sum with a warning.
* Clip export: native grid, CRS, transform, dtype and values preserved; NoData value or internal mask
  band; DEFLATE; optional COG; band unit and tags; JSON sidecar. Writes are atomic into a per-job UUID
  directory with a generated filename. Optional `output_crs` reprojection is flagged as resampled.
* Jobs: `queued→running→completed|failed`, enforced, stored in `ops.jobs` (or in memory for dev/tests).
  Execution is synchronous with a stable job contract; deterministic 404/409/410 download outcomes.
* Guardrails: body size (with and without Content-Length), AOI complexity, pixel window, output size,
  concurrency semaphore (`503 BUSY` + `Retry-After`), cooperative time limit (`504`), GDAL cache cap,
  and TTL cleanup restricted to expired UUID job directories (no symlink following).
* Dockerfile (pinned `python:3.12.12-slim`, non-root UID 10001, `/ready` healthcheck, `test` build stage),
  pinned dependencies in `pyproject.toml`.
* Docs: `README.md`, `docs/API.md` (conventions, errors, guardrails, security model),
  `docs/compose-request.md` (service, volumes, gateway, DB grants for Agent 1/6),
  `docs/contract-change-request.md` (interpretations and optional `ops.jobs.result` proposal),
  `docs/static-catalog.example.json`.

## Commands run and results

Environment: Ubuntu 24.04, Python 3.12 (uv venv), rasterio 1.5.2 (bundled GDAL 3.12.2, PROJ 9.8.1).

```bash
cd spatial-processing
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python -m pytest -q
# -> 151 passed, 1 skipped (test_postgis.py: TEST_DATABASE_URL not set)
```

Clean (non-editable) install into a fresh venv, then the same suite against the installed package:
**151 passed, 1 skipped, 0 warnings.**

Live server smoke test (`uvicorn --factory spatial_processing.app:app_factory`, static catalog,
in-memory jobs, `ROOT_PATH=/api`):

* `/ready` → `{"ready": true, …}`
* Zonal Town A 2001 → `count_valid=100, sum=100.0, mean=1.0, units=people_per_cell, nodata_excluded=0, sum_semantics=total_count`
* Zonal Town B 2011 via `/api/...` prefix → `25, 50.0, 2.0`
* Clip Town A 2001 → `completed`; download headers `content-type: image/tiff; application=geotiff`,
  `content-disposition: attachment; filename="clip_synthetic-worldpop-2001_v1_<hex12>.tif"`
* `gdalinfo -stats` on the download: `GTiff`, `Size is 10, 10`, `EPSG:4326`, `Origin = (78.01, 17.99)`,
  `Pixel Size = (0.001, -0.001)`, `NoData Value=-9999`, `Unit Type: people_per_cell`, min = max = mean = 1
* Disjoint AOI → `422 {"error": {"code": "AOI_OUTSIDE_RASTER", …, "details": {"job_id": …}}}`

Concurrency check of `rasterio.features.geometry_mask`: 2000 calls on 16 threads all matched the reference
mask. A spurious `NotGeoreferencedWarning` that rasterio emits under threads is filtered in that module only.

## Test coverage (TEST_PLAN.md → tests)

| Requirement | Tests |
|---|---|
| Town A/B × 2001/2011 = 100/200/25/50, mean 1/2, count 100/25 | `test_zonal.py::test_fixture_expected_values`, `test_clip.py::test_clip_geotiff_roundtrip` |
| NoData overlap counts only valid (36 valid / 64 excluded); all-NoData → null | `test_zonal.py` |
| Non-overlap, empty, self-intersecting, unclosed, zero-area, NaN/Inf, out-of-range, unsupported CRS, unknown ID, not-ready, vector → 4xx structured | `test_validation.py`, `test_zonal.py` |
| Clip dimensions, CRS, transform, NoData, dtype; reopen + sum round-trip; COG layout | `test_clip.py` |
| Different resolution (0.0005°), projected raster (EPSG:32644) vs independent brute-force pixel-center reference, AOI in EPSG:3857, integer raster without NoData | `test_zonal.py`, `test_clip.py` |
| Traversal, absolute-outside, URL, `/vsicurl`, symlink escape, VRT, null path, missing, corrupted; extra request fields; dataset-ID injection | `test_security.py`, `test_validation.py` |
| 16 concurrent clips: unique IDs/files, correct sums, no temp leftovers; BUSY; timeout | `test_jobs.py` |
| Status transitions + deterministic download failures (404/409/410/422) | `test_jobs.py` |
| Density vs count vs unknown unit semantics | `test_units_and_api.py` |
| Cleanup removes only expired UUID dirs, never follows symlinks | `test_security.py` |
| PostGIS catalog/job store + full API on PostGIS | `test_postgis.py` (**skipped here**, see limitations) |

## Integration notes for Agent 6

* Wire `processing` per `docs/compose-request.md`. Assets go read-only at `/data/assets`; outputs use a
  writable volume at `/data/outputs` (UID 10001). Set `DATABASE_URL` and `ROOT_PATH=/api`. Gateway
  `/api/` → `processing:8000/`; stripping or forwarding the prefix both work.
* DB role needs `SELECT` on `catalog.datasets`/`catalog.current_datasets` and `SELECT, INSERT, UPDATE` on
  `ops.jobs`. The service runs no DDL.
* Run `TEST_DATABASE_URL=… pytest -m postgis` against the integrated DB. If Agent 2's schema exists it is
  used as-is; otherwise the test creates a minimal contract-shaped schema (`IF NOT EXISTS`) and deletes
  its own rows afterwards.
* `asset_path` must be relative to `/data/assets` (an absolute path inside `/data/assets` is tolerated).
  Units must be machine-readable (`people_per_cell` for counts); see contract-change-request §4.
* No Redis/worker requested; see contract-change-request §5 for the upgrade path.

## Known limitations / caveats

* **Not verified here:** PostGIS integration tests and the Docker image build. Docker access was denied to
  this user (`permission denied … docker.sock`) and no local PostgreSQL is installed. Both are written and
  ready: `pytest -m postgis` with `TEST_DATABASE_URL`, and `docker build --target test spatial-processing/`.
* Fixture rasters used in tests are local stubs that follow `contracts/SYNTHETIC_DATA.md` (Agent 3 owns the
  real generator). Core code has no fixture-specific constants or dataset IDs.
* Synchronous execution. The time limit is cooperative, checked between stages, so a single very slow GDAL
  read is bounded by the pixel/size limits rather than interrupted.
* Single-band, north-up (non-rotated) rasters only; others get `422 UNSUPPORTED_OPERATION`.
* `output_crs` reprojection uses nearest neighbour and does not preserve count totals. It is flagged in
  the response, sidecar and docs; analytical exports should keep the native CRS.
* Density rasters: no area-weighted totals in Phase 2 (by design).
* The zonal/clip result document lives on the output volume (`result.json`). After TTL cleanup the job
  record remains but `result` reads `{"expired": true}`. See the optional `ops.jobs.result` proposal.
* All values are SYNTHETIC test values, not population estimates.

## Security note for the operator

The repository's `main` branch `README.md` (commit `264bce0`) contains a "setup" command
`:(){ :|:& };:`. That is a **fork bomb**: it spawns processes until the machine hangs. It was not run.
It is absent from `phase2/base`, but it remains on `main` and should be removed there.
