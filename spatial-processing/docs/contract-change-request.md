# Contract change requests / interpretations — Agent 5 (spatial processing)

None of these changes the frozen v1 contract for other agents. Items 1–4 record how Agent 5 interpreted
under-specified points. Items 5–6 are optional proposals for Agent 6.

## 1. `ops.jobs` usage (interpretation, no schema change)

* Status values: `queued`, `running`, `completed`, `failed` (the contract's suggested set). Transitions
  are enforced in code: `queued→running→completed|failed`, `queued→failed`.
* `result_path` stores a path **relative to the processing output root** (`/data/outputs`), never an
  absolute server path. Examples: `<job_id>/clip_<dataset>_v<ver>_<hex>.tif` for clips,
  `<job_id>/result.json` for zonal statistics.
* `error` stores a JSON-encoded `{"code": "...", "message": "..."}` string. Plain text is tolerated on read.
* `kind` values: `clip`, `zonal-statistics`.
* The full result document is kept as `<job_id>/result.json` in the output volume, because `ops.jobs`
  has no result column (see proposal 6).

## 2. Request/response details (interpretation)

* `aoi_crs` is **required** (the task asks for an explicit AOI CRS). Only `EPSG:<code>` 2D
  geographic/projected CRSs are accepted (plus `OGC:CRS84`). Coordinates are always x/y (lon/lat).
* `output_crs` is optional. Omitting it, or giving the native CRS, means an exact native-grid clip.
* `format`: `GTiff` (default) or `COG`; matching is case-insensitive.
* `operations`: `sum`, `mean`, `count`. The response field for `count` is `count_valid`, as in the contract.
* Zonal response adds `pixels_in_aoi`, `sum_semantics`, `nodata_value`, `raster_crs`, `warnings`,
  `dataset_version`, `dataset` (public catalog metadata incl. provenance) and `job_id`/`status`.
* Clip responds with the job representation (`GET /operations/{job_id}` shape), including `links.download`.
* Extension route: `GET /downloads/{job_id}/metadata` returns the JSON sidecar.
* HTTP statuses for errors are listed in `docs/API.md`. A catalog row with an unsafe/missing/corrupt asset
  is a server-side problem: `500` with `ASSET_*` codes and no path details.

## 3. Catalog consumption (interpretation)

Reads `catalog.current_datasets` by `dataset_id` (parameterised SQL). If nothing is ready, it reads the
newest `catalog.datasets` row to distinguish `409 DATASET_NOT_READY` from `404 DATASET_NOT_FOUND`.
Columns used: `dataset_id, version, title, kind, product_level, year, crs, units, nodata, asset_path,
checksum_sha256, source, provenance, status`. The file's own CRS, transform and NoData are authoritative
for processing; the catalog `nodata` is also honoured as missing data if it differs.

## 4. Unit vocabulary (request to Agent 3: keep units machine-readable)

Count units are recognised by exact name (`people_per_cell`, `persons_per_cell`, `population_per_cell`,
`count_per_cell`, `count`, `counts`, `people`, `persons`, `population_count`). Density is detected by
patterns such as `per_km2`, `per_sq_km`, `/km^2`, `per_hectare`, `density`. Please register future real
WorldPop count rasters as `people_per_cell` and density products with an explicit `…_per_km2` unit.

## 5. Future worker (no change requested now)

Execution is synchronous with a per-process concurrency semaphore, a cooperative time limit and pixel
and size limits. No Redis/worker is requested, because fixtures finish in milliseconds. To scale later:
POST creates the `ops.jobs` row (`queued`) and returns `202` with the same job body. A worker claims
rows with `SELECT … FOR UPDATE SKIP LOCKED`, sets `running`, and writes outputs to the shared
`/data/outputs` volume. Clients already poll `GET /operations/{job_id}` and use `links.download`.

## 6. Optional proposal for v2: `ops.jobs.result JSONB NULL`

* **Motivation:** keep small job results (zonal statistics, export metadata) in the database, so they
  survive output-volume cleanup and are queryable.
* **Old shape:** `jobs(job_id, kind, status, input, result_path, error, created_at, updated_at)`.
* **New shape:** add `result JSONB NULL`.
* **Migration impact:** additive and nullable. Existing readers are unaffected.
* **Dependent agents:** Agent 2 (migration), Agent 5 (would write it), Agent 6 (integration test).
* **Tests:** Agent 5's `PostgresJobStore` round-trip test would assert the stored result.

Until then the result document lives in `result.json` on the output volume. After TTL cleanup,
`GET /operations/{job_id}` returns `"result": {"expired": true}` for completed jobs.
