# Contract change request — Agent 4 (OGC services)

Status: **proposed**, non-blocking for Phase 2. Agent 4's branch works with the frozen v1 contract
unchanged; these changes remove two ambiguities once more than the synthetic data is loaded.

## CCR-4.1 Link vector catalog datasets to their feature rows

**Motivation.** `catalog.datasets` registers `synthetic-towns` as `kind = 'vector'`, but the frozen
`geo.admin_units (unit_id, name, level, geom)` has no column naming the dataset a row belongs to. With
one vector dataset this is harmless; with two (e.g. synthetic towns + real district boundaries) the OGC
publisher cannot tell which rows belong to which collection and would publish the whole table twice.

**Old shape.** `geo.admin_units(unit_id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT, geom geometry(MultiPolygon,4326))`.

**New shape (proposal).**

```sql
ALTER TABLE geo.admin_units ADD COLUMN dataset_id TEXT;      -- logical catalog id, no version
CREATE INDEX admin_units_dataset_id_idx ON geo.admin_units (dataset_id);
-- optional once backfilled: NOT NULL, and unit_id unique per dataset_id instead of globally
```

Ingestion (Agent 3) writes the manifest's `dataset_id` into each imported feature row.

**Behaviour in Agent 4's code (already implemented, no further change needed).** The publisher
(`ogc-services/pygeoapi/cgis_ogc/publish.py`) introspects the table: if a `dataset_id` column exists,
each vector collection is scoped with `dataset_id = <catalog id>`. Until then it accepts an optional
provenance hint `{"ogc_publication": {"filter": {"level": "synthetic_town"}}}` (equality on existing
columns only), and otherwise publishes the whole table with a warning in the log.

**Migration impact.** Additive column; existing rows can be backfilled with
`UPDATE geo.admin_units SET dataset_id = 'synthetic-towns' WHERE level = 'synthetic_town'`. No change to
`catalog.datasets` or `catalog.current_datasets`.

**Dependent agents.** Agent 2 (migration), Agent 3 (populate on import), Agent 4 (consumer, ready),
Agent 5 (may use it to resolve AOIs by dataset), Agent 6 (integration decision).

**Tests.** `ogc-services/tests/unit/test_publish.py::test_static_filter_prefers_dataset_id_column`;
`ogc-services/tests/integration/test_publication.py::test_new_vector_dataset_is_published_and_scoped`
(exercises the scoped provider through the provenance hint).

## CCR-4.2 Read-only database role for publication services

**Motivation.** `ogc` and `tiles` only `SELECT` from `catalog.current_datasets`, `geo.admin_units` and
`information_schema.columns`. Running them with the owner role is more privilege than needed.

**Proposal.** Agent 2 adds a role (e.g. `cgis_reader`) with `USAGE` on schemas `catalog`, `geo` and
`SELECT` on `catalog.current_datasets` and `geo.admin_units`; Agent 1 exposes it as
`OGC_DB_USER`/`OGC_DB_PASSWORD` (see `ogc-services/docs/gateway-request.md`). Both services also
open their sessions read-only.

**Migration impact.** None for data; one `CREATE ROLE`/`GRANT` migration. **Dependent agents.** 1, 2, 6.
