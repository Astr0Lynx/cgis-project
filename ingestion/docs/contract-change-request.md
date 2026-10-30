# Contract change request — Agent 3 (ingestion)

Status: **proposed**, for Agent 2 (schema owner) and Agent 6 (integration). Every item below is
**additive or a clarification**. The ingestion code already works against the frozen v1 schema as
written, so no item blocks integration. The importer detects the optional columns at runtime.

## CCR-3.1 — Link `geo.admin_units` rows to their catalog dataset (additive, optional)

**Motivation.** The v1 contract has `geo.admin_units(unit_id, name, level, geom)` with no link to the
`catalog.datasets` record that produced each row. Agent 4 needs to publish the `synthetic-towns`
collection (and, later, real boundary sets) as an OGC API Features collection without a hard-coded
ID list. The importer also needs to stop two datasets claiming the same `unit_id`.

**Old shape.** `geo.admin_units(unit_id TEXT PK, name TEXT NOT NULL, level TEXT, geom geometry(MultiPolygon,4326))`.

**New shape.** Add nullable columns:

```sql
ALTER TABLE geo.admin_units
  ADD COLUMN dataset_id      TEXT,
  ADD COLUMN dataset_version INTEGER,
  ADD COLUMN properties      JSONB,     -- original feature attributes
  ADD COLUMN source          TEXT;
CREATE INDEX ON geo.admin_units (dataset_id);
```

(Optionally add `FOREIGN KEY (dataset_id, dataset_version) REFERENCES catalog.datasets`. It is
compatible: the catalog row already exists, as `staged`, when the importer inserts the units.)

**Behaviour once present.** The importer fills all four columns, and it refuses with `unit_id_conflict`
if a `unit_id` already belongs to a different `dataset_id`. This is tested in
`test_optional_admin_unit_columns_are_populated`.

**Without it (v1 fallback).** The catalog row for each vector dataset lists its unit IDs in
`provenance->'vector'->'unit_ids'`. Agent 4 can select
`WHERE unit_id IN (SELECT jsonb_array_elements_text(provenance->'vector'->'unit_ids') FROM catalog.current_datasets WHERE dataset_id = $1)`.

**Migration impact.** None for existing rows (columns are nullable). **Dependents:** Agent 4 (collection
filter), Agent 6. **Tests:** above, plus the whole DB suite with `INGESTION_TEST_SCHEMA=existing`.

## CCR-3.2 — `catalog.datasets.units` must be nullable (clarification)

The contract marks `year`, `resolution_*`, `nodata` and `asset_path` as nullable but says nothing
about `units`. Vector datasets have no pixel units, so the importer writes `units = NULL` for them.
**Request:** keep `units` nullable, or enforce `units IS NOT NULL` only `WHERE kind = 'raster'`
(the importer already requires `units` for rasters).

## CCR-3.3 — Failure details live in `provenance` (clarification, no change requested)

There is no `error` column on `catalog.datasets`. For `status = 'failed'` rows, the importer stores
`provenance->'ingestion'->'error' = {"code": ..., "message": ...}`. Consumers must keep filtering on
`status = 'ready'` (as `catalog.current_datasets` does). A failure that happens before any metadata is
known, such as an unreadable file, is reported by the CLI but may not create a row, because `crs`/`bbox`
are expected to be `NOT NULL`.

## CCR-3.4 — Vector versioning semantics (clarification)

`geo.admin_units.unit_id` is a global primary key, so importing `synthetic-towns` v2 **updates** the
rows created by v1 (latest import wins). Older versions remain in `catalog.datasets` and as immutable
GeoJSON assets under `/data/assets/vectors/<id>/v<n>/`. If Agent 2 prefers per-version unit rows, the
primary key would need to become `(unit_id, dataset_version)` or similar. That is a breaking change
for Agents 4 and 5 and is **not** requested for v1.

## Schema assumptions the importer relies on (please keep)

- `PRIMARY KEY (dataset_id, version)` (used by `INSERT … ON CONFLICT (dataset_id, version)`).
- `geo.admin_units` has a unique constraint on `unit_id` (used by `ON CONFLICT (unit_id)`).
- `status` accepts `'staged' | 'ready' | 'failed'`. A text column, CHECK or enum all work; the
  importer compares `status::text`.
- `provenance` is `JSONB` (TEXT also works through an assignment cast; JSONB is preferred).
- `bbox` is `geometry(Polygon,4326)`; the importer writes `ST_MakeEnvelope(w,s,e,n,4326)`.
- `created_at`/`updated_at` are written explicitly (`now()`), so defaults and triggers are optional.
