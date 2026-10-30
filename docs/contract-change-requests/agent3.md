# Agent 3 (ingestion) — contract change requests

The full text, with motivation, old/new shape, migration impact, dependents and tests, is in
[`ingestion/docs/contract-change-request.md`](../../ingestion/docs/contract-change-request.md).

| ID | Kind | Summary | Blocking? |
|---|---|---|---|
| CCR-3.1 | additive, optional | Add nullable `dataset_id`, `dataset_version`, `properties JSONB`, `source` to `geo.admin_units`. The importer fills them when present. | No (fallback: `provenance.vector.unit_ids`) |
| CCR-3.2 | clarification | Keep `catalog.datasets.units` nullable (vectors write NULL) | No, if nullable |
| CCR-3.3 | clarification | Failed-import details are stored in `provenance.ingestion.error` | No |
| CCR-3.4 | clarification | `geo.admin_units` holds the latest imported version of each `unit_id` | No |

Compose/env wiring requested from Agent 1: [`ingestion/docs/compose-request.md`](../../ingestion/docs/compose-request.md).
