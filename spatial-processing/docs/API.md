# Spatial processing API (custom, non-OGC)

Public base URL: `http://localhost:8080/api` (gateway). The service itself listens on `processing:8000`
with unprefixed routes; `ROOT_PATH=/api` makes links and the OpenAPI `servers` entry use the public prefix.
Both forwarded (`/api/operations/...`) and stripped (`/operations/...`) paths are routed.

Interactive docs: `/api/docs` (Swagger UI), `/api/redoc`; machine-readable: `/api/openapi.json`
(snapshot in [`openapi.json`](openapi.json)).

These are project-specific routes. They are **not** OGC API Processes/Coverages endpoints.

| Method | Path | Purpose |
|---|---|---|
| POST | `/operations/clip` | Clip a catalogued raster to an AOI; export GeoTIFF or COG |
| POST | `/operations/zonal-statistics` | `sum`, `mean`, `count` of valid pixels in an AOI |
| GET | `/operations/{job_id}` | Job status, input, result, error |
| GET | `/downloads/{job_id}` | Download the clipped GeoTIFF/COG of a completed clip job |
| GET | `/downloads/{job_id}/metadata` | JSON metadata sidecar of that export (extension) |
| GET | `/health` | Liveness (process up) |
| GET | `/ready` | Readiness: asset root, writable output root, catalog and job store reachable (503 otherwise) |

## Requests

Common fields (unknown fields are rejected — a client cannot pass a path or URL):

| Field | Type | Notes |
|---|---|---|
| `dataset_id` | string | Catalog slug `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, resolved via `catalog.current_datasets` (highest **ready** version) |
| `aoi` | GeoJSON | `Polygon`, `MultiPolygon`, or a `Feature` wrapping one. x/y order (lon/lat) regardless of CRS axis order. Rings closed, ≥ 4 positions, finite numbers, OGC-valid (no self-intersections), non-zero area |
| `aoi_crs` | string | **Required.** `EPSG:<code>` naming a 2D geographic or projected CRS (`OGC:CRS84` accepted as lon/lat WGS84) |

Clip only: `output_crs` (optional `EPSG:<code>`; omit for the native grid) and `format` (`GTiff` default, or `COG`).
Zonal only: `operations` (optional subset of `sum`, `mean`, `count`; default all).

```bash
curl -s -X POST http://localhost:8080/api/operations/zonal-statistics \
  -H 'content-type: application/json' \
  -d '{"dataset_id":"synthetic-worldpop-2001","aoi_crs":"EPSG:4326",
       "aoi":{"type":"Polygon","coordinates":[[[78.01,17.98],[78.02,17.98],[78.02,17.99],[78.01,17.99],[78.01,17.98]]]}}'
```

### Zonal statistics response (Town A, 2001; abridged)

```json
{
  "job_id": "1f0c…", "status": "completed",
  "dataset_id": "synthetic-worldpop-2001", "dataset_version": 1,
  "count_valid": 100, "sum": 100.0, "mean": 1.0,
  "units": "people_per_cell", "sum_semantics": "total_count",
  "pixels_in_aoi": 100, "nodata_excluded": 0, "nodata_value": -9999.0,
  "aoi_crs": "EPSG:4326", "raster_crs": "EPSG:4326",
  "pixel_selection": {"rule": "pixel_center", "all_touched": false, "description": "…"},
  "operations": ["sum", "mean", "count"], "warnings": [],
  "dataset": {"title": "SYNTHETIC …", "provenance": {"synthetic": true}, "…": "…"},
  "computed_at": "2026-10-30T17:27:00+00:00"
}
```

Integer rasters return an integer `sum`; float rasters return a float. When every selected pixel is NoData,
`count_valid` is 0 and `sum`/`mean` are `null` (never 0).

### Clip response = job representation

```json
{
  "job_id": "9b6a…", "kind": "clip", "status": "completed",
  "created_at": "…", "updated_at": "…",
  "input": {"dataset_id": "synthetic-worldpop-2001", "dataset_version": 1, "aoi": {…}, "aoi_crs": "EPSG:4326", "output_crs": "EPSG:4326", "format": "GTiff"},
  "result": {
    "filename": "clip_synthetic-worldpop-2001_v1_9b6ab5e9371e.tif",
    "media_type": "image/tiff; application=geotiff",
    "pixels_in_aoi": 100, "count_valid": 100, "nodata_excluded": 0,
    "output": {"width": 10, "height": 10, "crs": "EPSG:4326",
               "transform_gdal": [78.01, 0.001, 0.0, 17.99, 0.0, -0.001],
               "nodata": -9999.0, "dtype": "float32", "mask": "nodata_value",
               "count_valid": 100, "sum_valid": 100.0, "resampled": false, "resampling": "none",
               "format": "GTiff", "driver_layout": "GTiff", "size_bytes": 1234},
    "warnings": [], "download_url": "/api/downloads/9b6a…", "metadata_url": "/api/downloads/9b6a…/metadata"
  },
  "error": null,
  "links": {"self": "/api/operations/9b6a…", "download": "/api/downloads/9b6a…", "metadata": "/api/downloads/9b6a…/metadata"}
}
```

`output.*` is measured by **re-opening the written file**, so it doubles as a round-trip check.

## Analytical conventions

* **Pixel selection:** a pixel is in the AOI iff its **center** lies inside the polygon
  (`rasterio.features.geometry_mask(all_touched=False)`). Pixels whose center lies exactly on the
  boundary follow GDAL's rasterization rule. A visualization-oriented clip (e.g. TiTiler cutline, or
  `all_touched=true`) can include more edge pixels; do not use it for statistics.
* **CRS:** the AOI is transformed (edges densified) into the raster's **native** CRS; rasters are never
  resampled for statistics. Clips on the native grid keep the source CRS definition, pixel size and
  pixel alignment; the output extent is the bounding box of the selected pixels.
* **NoData:** the file's NoData value and mask, the catalog's `nodata`, and non-finite floats are all
  treated as missing. Missing pixels are excluded and counted in `nodata_excluded`; clips write them
  (and pixels outside the AOI) as the NoData value, or as an internal mask band when the source has no
  NoData value.
* **Values:** clips copy native values and dtype unchanged; DEFLATE compression (lossless), with
  floating-point/horizontal predictor. Band unit, scale/offset and tags (`DATASET_ID`, `DATASET_VERSION`,
  `UNITS`, `PIXEL_SELECTION`, `SOURCE`) are written to the GeoTIFF.
* **Reprojection:** only when `output_crs` differs from the native CRS. It uses nearest neighbour onto a
  new grid, sets `output.resampled=true`, and adds a warning: totals of count rasters are **not** preserved.
* **Units (`sum_semantics`):**
  * `total_count`: count units (`people_per_cell`, `count`, …). The sum is a total count.
  * `not_applicable_density`: density units (`…per_km2`, `…/km^2`, `…per_hectare`, `density`). The sum is
    omitted with a warning. An explicitly requested `sum` is rejected with `422 UNIT_SEMANTICS`, because
    area-weighted totals are out of scope for Phase 2.
  * `sum_of_cell_values`: unknown or missing units. The plain sum is returned with a warning that it is not a population count.

## Jobs

Status: `queued → running → completed | failed` (no other transitions). Operations execute synchronously
inside the request, so the POST already returns `completed`. Clients should still read `status`, which
lets a future worker return `queued`/`running` without an API change.

* Malformed input, unknown or not-ready datasets, and `BUSY` are rejected **before** a job is created.
* After acceptance, failures mark the job `failed` with `{code, message}`, remove partial outputs, and
  the error response includes `details.job_id`.

Download outcomes are deterministic:

| Situation | Status | Code |
|---|---|---|
| unknown job | 404 | `JOB_NOT_FOUND` |
| malformed job id | 422 | `INVALID_REQUEST` |
| job is a zonal-statistics job | 409 | `NO_DOWNLOAD` |
| job failed | 409 | `JOB_FAILED` |
| job queued/running | 409 | `JOB_NOT_COMPLETED` |
| output removed by TTL cleanup | 410 | `OUTPUT_EXPIRED` |
| success | 200 | `Content-Type: image/tiff; application=geotiff` (`; profile=cloud-optimized` for COG), `Content-Disposition: attachment; filename="clip_<dataset>_v<version>_<job12hex>.tif"` |

## Errors

All errors look like `{"error": {"code": "…", "message": "…", "details": {…}?}}`. Messages never include
server paths, SQL or stack traces.

| Status | Codes |
|---|---|
| 404 | `DATASET_NOT_FOUND`, `JOB_NOT_FOUND`, `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `DATASET_NOT_READY`, `JOB_NOT_COMPLETED`, `JOB_FAILED`, `NO_DOWNLOAD` |
| 410 | `OUTPUT_EXPIRED` |
| 413 | `REQUEST_TOO_LARGE`, `AOI_TOO_COMPLEX`, `AOI_TOO_LARGE`, `OUTPUT_TOO_LARGE` |
| 422 | `INVALID_REQUEST`, `INVALID_DATASET_ID`, `INVALID_GEOMETRY`, `EMPTY_GEOMETRY`, `UNSUPPORTED_GEOMETRY_TYPE`, `NON_FINITE_COORDINATES`, `COORDINATES_OUT_OF_RANGE`, `UNSUPPORTED_CRS`, `UNSUPPORTED_FORMAT`, `UNSUPPORTED_OPERATION`, `UNIT_SEMANTICS`, `AOI_OUTSIDE_RASTER`, `NO_PIXELS_SELECTED`, `DATASET_NOT_RASTER` |
| 500 | `ASSET_PATH_REJECTED`, `ASSET_MISSING`, `ASSET_UNREADABLE`, `INTERNAL_ERROR` |
| 503 | `BUSY` (with `Retry-After`), `CATALOG_UNAVAILABLE`, `JOB_STORE_UNAVAILABLE` |
| 504 | `JOB_TIMEOUT` |

## Guardrails (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `MAX_REQUEST_BYTES` | 1000000 | Request body limit (with or without `Content-Length`) |
| `MAX_AOI_VERTICES` / `MAX_AOI_PARTS` | 10000 / 100 | AOI complexity |
| `MAX_WINDOW_PIXELS` | 25000000 | Pixels read for one operation (memory bound) |
| `MAX_OUTPUT_BYTES` | 536870912 | Uncompressed export size |
| `MAX_CONCURRENT_JOBS` | 4 | Concurrent operations per process |
| `QUEUE_WAIT_SECONDS` | 10 | Wait for a slot before `503 BUSY` |
| `MAX_JOB_SECONDS` | 120 | Cooperative time limit, checked between stages → `504 JOB_TIMEOUT` |
| `OUTPUT_TTL_SECONDS` | 86400 | Age after which job output directories are deleted |
| `CLEANUP_INTERVAL_SECONDS` | 600 | Minimum interval between cleanup sweeps (also runs at startup) |
| `GDAL_CACHEMAX_MB` | 256 | GDAL block cache |

## Security model

* Raster locations come only from the catalog. Each `asset_path` is re-validated: it must be relative to
  `ASSET_ROOT` (or absolute inside it), with no `..`, URL scheme, `/vsi*` prefix, backslash or NUL. It must
  be a `.tif`/`.tiff` regular file whose real path (symlinks resolved) stays inside the root, and it is
  opened with the GTiff driver only (a VRT cannot pull in other files).
* Outputs go to `OUTPUT_ROOT/<job-uuid>/` with generated names, written to a temp file and atomically
  renamed. Downloads resolve only stored relative paths inside `OUTPUT_ROOT`.
* Cleanup removes only expired, canonical-UUID-named real directories directly under `OUTPUT_ROOT`. It
  unlinks entries without following symlinks and skips active jobs and anything unexpected.
* There are no write routes besides the operations themselves, and no route accepts a path or URL.
