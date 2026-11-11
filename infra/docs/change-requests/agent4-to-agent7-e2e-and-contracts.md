# Change requests: Agent 4 → Agent 7 (e2e test updates, contract records)

**From:** Agent 4 (`completion/agent4-platform`). **To:** Agent 7 (owner of `tests/e2e/`, `contracts/`
records via a versioned decision, integration). Agent 4 has **not** edited `tests/e2e/` or `contracts/`.

## CR-A4-7.1 — allow the async-job cancel route in the e2e route allow-list (required)

`tests/e2e/test_50_negative.py::test_processing_api_has_no_catalog_mutation_routes` pins the processing
OpenAPI paths. Completion P3 adds one route: `POST /operations/{job_id}/cancel` (cancels a queued/running
**background job**; it does not touch the catalog). With the current allow-list this one test fails:

```diff
-    allowed = {"/operations/clip", "/operations/zonal-statistics", "/operations/{job_id}", "/downloads/{job_id}",
-               "/downloads/{job_id}/metadata", "/health", "/ready"}
+    allowed = {"/operations/clip", "/operations/zonal-statistics", "/operations/{job_id}", "/downloads/{job_id}",
+               "/downloads/{job_id}/metadata", "/health", "/ready", "/operations/{job_id}/cancel"}
     ...
         if "post" in ops:
-            assert path in ("/operations/clip", "/operations/zonal-statistics")
+            assert path in ("/operations/clip", "/operations/zonal-statistics", "/operations/{job_id}/cancel")
```

The rest of that test (no DELETE/PUT, no `/datasets`, no `/import`) stays valid and passes. Behaviour of
the existing routes is unchanged: `mode` defaults to `sync`, so every Phase 2 request and response is the
same. Tests of the new behaviour: `spatial-processing/tests/test_async_blockwise.py` (25),
`spatial-processing/tests/test_postgis.py` (2 new, PostGIS).

## CR-A4-7.2 — record the `geo.admin_units` identity change (contract decision)

`contracts/SHARED_CONTRACTS.md` (frozen Phase 2 v1) says `admin_units(unit_id TEXT PRIMARY KEY, ...)`. The
completion contract (`shared/SHARED_CONTRACTS.md` §5, acceptance P1) requires version-aware identity.
Implemented in `database/migrations/core/0007_admin_units_versioned`:

| | Before (v1) | After |
|---|---|---|
| Primary key | `unit_id` | `unit_key bigint` (surrogate) |
| Identity | `unit_id` | `UNIQUE NULLS NOT DISTINCT (dataset_id, dataset_version, unit_id)` |
| Columns | unchanged | + `unit_key`; every contract column kept with its type |
| Reimport of v2 | overwrote v1 rows | adds v2 rows; v1 kept; `geo.current_admin_units` serves the current ready version |
| Same `unit_id` in two datasets | rejected (`unit_id_conflict`) | allowed |
| Readers by `unit_id` alone | 1 row | possibly 1 row per version/dataset: read `geo.current_admin_units` or filter by `dataset_id` |

Backward compatibility: additive for readers (no column removed or retyped; spatial primitives keep
their signatures). `tests/e2e/test_10_catalog.py` queries by `unit_id IN ('town-a','town-b')` and still gets
exactly the synthetic rows (only v1 exists). Migration/rollback/backfill evidence:
`database/tests/test_versioned_units.py` (upgrade from a `0006` database with v1 rows; rollback refuses
while versions coexist). Please record it as a versioned contract decision (proposed id **D-A4-1**).

## CR-A4-7.3 — integration settings (for the integrated branch)

* Set `COMPOSE_PROFILES=app` in `.env.example` once `application-api/` is merged (left empty on this
  branch because the directory does not exist here). `sh infra/scripts/init-env.sh` now appends missing
  keys to an existing `.env`, so upgrading an old `.env` is one command.
* Decision **D-A5-1** (read routes `public` vs `protected`) is now a switch: `GATEWAY_READ_ACCESS`.
  Default `public` (CITE runs anonymously). Choose `protected` whenever restricted datasets are published.
* Agent 2 CR "national Level 1 import" is answered by the benchmark in
  `infra/COMPLETION_HANDOFF_REPORT.md`; the `science` profile service `level1` is in `compose.yaml`.
