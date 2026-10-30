# Frozen v1 shared interface contracts

These are the baseline contracts. Agent 1 commits them first to `phase2/base`. Agents 2–5 build against them without changing them independently. Agent 6 may issue a coordinated v2 only when merging, with migration/tests and written rationale.

## Dataset catalog

PostGIS schema `catalog`, table `datasets` with composite primary key `(dataset_id, version)` and a read-only view `catalog.current_datasets` selecting the highest **ready** version of each ID. The view has the same public columns as the table. Agents 4/5 resolve a bare `dataset_id` through this view; Agent 3 writes versioned records to the table.

| Column | Type / semantics |
|---|---|
| `dataset_id` | `TEXT`, immutable logical asset slug; composite PK with version |
| `version` | positive integer; composite PK with dataset_id |
| `title`, `description` | nonempty text / optional text |
| `kind` | `raster` or `vector` |
| `product_level` | `source`, `level1`, `level2`, `other` |
| `year` | nullable integer |
| `crs` | text `EPSG:<number>` representing native asset CRS |
| `bbox` | `geometry(Polygon,4326)`; always searchable with GiST |
| `resolution_x`, `resolution_y` | nullable positive numeric pixel sizes in native CRS units |
| `units` | e.g. `people_per_cell`; never assume counts/densities equivalent |
| `nodata` | nullable numeric |
| `asset_path` | nullable server-side path relative to `/data/assets`, never a client-provided arbitrary absolute path |
| `checksum_sha256` | content checksum, unique as appropriate with version semantics |
| `source`, `provenance` | source label and JSON metadata / provenance |
| `status` | `staged`, `ready`, or `failed` |
| `created_at`, `updated_at` | timezone-aware timestamps |

Agent 2 may normalize storage internally only if both `catalog.datasets` write behavior and `catalog.current_datasets` read behavior above remain compatible and documented.

PostGIS schema `geo`: `admin_units(unit_id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT, geom geometry(MultiPolygon,4326))`, with GiST index on `geom`. Normalize Polygon inputs to MultiPolygon; preserve attributes/provenance as needed.

PostGIS schema `ops`: `jobs(job_id UUID, kind TEXT, status TEXT, input JSONB, result_path TEXT NULL, error TEXT NULL, created_at, updated_at)`; choose and document status values, ideally `queued`, `running`, `completed`, `failed`.

## Fixture IDs and expected data contract

- `synthetic-worldpop-2001` (raster, year 2001, `people_per_cell`).
- `synthetic-worldpop-2011` (raster, year 2011, `people_per_cell`).
- `synthetic-towns` (vector collection with Town A and Town B).
- Files and precise polygons are specified in `SYNTHETIC_DATA.md`.

## Ingestion contract

Python CLI callable inside Compose, e.g. `python -m ingestion.cli import --manifest /data/fixtures/manifest.json`. Should be idempotent for identical ID/version/checksum, reject conflicting re-uploads with clear error, create/verify COG, persist catalog record, and leave dataset `ready` only after asset+metadata validation. A higher ready version becomes the view's current dataset without deleting older versions.

Manifest JSON MUST accept at least:

```json
{
  "dataset_id": "synthetic-worldpop-2001",
  "version": 1,
  "title": "SYNTHETIC population raster (2001)",
  "kind": "raster",
  "product_level": "source",
  "year": 2001,
  "units": "people_per_cell",
  "source": "deterministic-fixture",
  "file": "/data/fixtures/synthetic_2001.tif"
}
```

Coordinates, CRS, bounds, NoData, and resolution are read from the file rather than trusted from manifest. Vector manifest follows same shared shape with `kind=vector`.

## Public HTTP API surface

Standard: `GET /ogc/`, `GET /ogc/collections`, `GET /ogc/collections/{collectionId}`, `GET /ogc/collections/{collectionId}/items` and item-by-id, with documented bbox filtering and pagination. These are illustrative common OGC API Features routes; implement according to provider-generated conformance/openAPI, not handcrafted lookalikes.

Raster visualization: tile URL/TileJSON under `/tiles/` as supported by TiTiler, linked to server-owned raster assets; additionally an OGC API Tiles interface under `/ogc/` for published raster collections, using the standard's real discovery/tileset/TileMatrixSet metadata routes. Direct arbitrary file or remote URL access must be restricted. Agent 4 tests standards conformance and labels `/tiles/` as renderer-specific when appropriate.

Custom processing routes under gateway `/api`:

`POST /api/operations/clip` body:

```json
{
  "dataset_id": "synthetic-worldpop-2001",
  "aoi": {"type": "Polygon", "coordinates": [[[78.01,17.98],[78.02,17.98],[78.02,17.99],[78.01,17.99],[78.01,17.98]]]},
  "aoi_crs": "EPSG:4326",
  "output_crs": "EPSG:4326",
  "format": "GTiff"
}
```

`POST /api/operations/zonal-statistics` accepts `dataset_id`, `aoi`, `aoi_crs`, optional requested operations (`sum`, `mean`, `count`). Output includes `dataset_id`, `count_valid`, `sum`, `mean`, `units`, `nodata_excluded`, `aoi_crs`, and explicit pixel-selection semantics. For people-per-cell population rasters, sum is population count; never sum arbitrary density rasters without unit conversion.

`GET /api/operations/{job_id}` returns status/result/error; `GET /api/downloads/{job_id}` downloads a completed export with correct Content-Type and safe generated filename. Small operations may complete synchronously but must also have a stable job/status contract, or Agent 5 can propose a documented simpler v1 with Agent 6 approval.

## Error and quality contract

- Errors return non-200 statuses and structured `{ "error": { "code": "...", "message": "..." } }` where the custom API controls response shapes.
- Invalid CRS, missing datasets, bad/empty polygon, unsupported format, and non-overlapping extents return explicit errors.
- HTTP/API externally expose EPSG:4326 where specified; transform queries to native raster CRS internally.
- Never silently coerce missing pixels or NoData to zero; preserve/reports excluded cells.
- Prefer integer population-sum semantics for synthetic count rasters; document resampling behavior when exports change grid.

## Contract change procedure

Agents 2–5 write an explicit proposed change in `docs/contract-change-requests/agentN.md` with motivation, old/new shape, migration impact, dependent agents, and tests. They must not silently fork schemas, routes, file mounts, or fixture semantics. Agent 6 resolves changes during integration; if required to unblock parallel work, Agent 1 publishes a new baseline revision and informs all others.
