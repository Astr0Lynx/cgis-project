# Agent 2 contract notes and change requests

None of these changes are **required** for v1 integration. The frozen column names and types of
`catalog.datasets`, `catalog.current_datasets`, `geo.admin_units` and `ops.jobs` are implemented
exactly as written. This file records (A) additive choices that stay inside the contract, which
Agent 6 should know about, and (B) proposals for a coordinated v2.

## A. Additive, contract-compatible choices (no action needed)

1. **`geo.admin_units` extra columns:** `properties jsonb`, `dataset_id`, `dataset_version` (FK to
   `catalog.datasets`), `source`, `provenance jsonb`, `created_at`, `updated_at`. The contract allows
   preserving attributes and provenance. The four contract columns are unchanged, and `geom` is
   `NOT NULL`.
2. **`ops.jobs` extra columns:** `result jsonb` (inline results, e.g. zonal statistics, for jobs
   with no file), `started_at`, `finished_at`. Status values are `queued`, `running`, `completed`,
   `failed`, which is the contract's suggestion.
3. **Catalog constraints the contract leaves open:**
   - a `dataset_id` slug pattern: `^[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$`;
   - `year` must be between 1800 and 2200;
   - `crs` must exist in `spatial_ref_sys`;
   - a `ready` row must have `bbox` and `checksum_sha256`, and a ready raster must also have
     `asset_path`, `units` and `resolution_*`;
   - ready rows are immutable except for `ready → failed` (withdrawal);
   - `nodata` is `double precision` (it can hold NaN), and `resolution_*` are `double precision`.
4. **`checksum_sha256` uniqueness:** this is enforced per `(dataset_id, version)` through
   `catalog.register_dataset` (same checksum is idempotent, a different checksum raises 23505).
   The same content **may** appear under a new version, so a metadata-only revision is possible.
   No cross-version unique index is added.

## B. Proposals for v2 (Agent 6 to decide)

### B1. Version-aware unit identity

- **Motivation:** `geo.admin_units.unit_id` is a global primary key. Importing version 2 of a
  boundary dataset therefore *replaces* the version 1 units, which then disagree with the still-ready
  `synthetic-towns` v1 catalog row. A `unit_id` that belongs to another dataset is already rejected,
  never silently overwritten.
- **Old shape:** `PRIMARY KEY (unit_id)`.
- **New shape:** `PRIMARY KEY (dataset_id, dataset_version, unit_id)`, plus a view
  `geo.current_admin_units` that joins to `catalog.current_datasets`.
- **Migration impact:** add a new core migration that backfills `dataset_id`/`dataset_version`
  (`NOT NULL`) and swaps the PK. OGC collections would read the view.
- **Dependent agents:** 3 (importer upsert key), 4 (pygeoapi provider table/view), 5 (AOI lookup by
  unit_id).
- **Tests:** two versions coexist; the current view returns only the highest ready version.

### B2. Catalog status message

- **Motivation:** when ingestion fails, the reason is recorded in `ops.jobs.error`, not on the
  dataset row. A nullable `status_message text` on `catalog.datasets` would make `failed` rows
  self-explaining.
- **Impact:** an additive column, which also appears in `current_datasets`. That breaks the
  "same public columns" equality only if consumers use `SELECT *` positionally.
- **Dependent agents:** 3 (writes it) and 6.
