# CyberGIS Project — Phase 2 geospatial data platform

Containerized geospatial data platform (PostGIS, ingestion, OGC API publication, raster tiles, spatial
processing) developed against **deterministic synthetic fixtures** — not real population data.

## Repository layout

| Path | Owner | Contents |
|---|---|---|
| `contracts/` | frozen v1 | Authoritative shared interface contracts — start here |
| `infra/`, `compose.yaml`, `.env.example`, `Makefile` | Agent 1 | Docker Compose stack, gateway, task commands |
| `database/` | Agent 2 | PostGIS schema and migrations |
| `ingestion/` | Agent 3 | Validation, COG conversion, catalog registration, fixture generator |
| `ogc-services/` | Agent 4 | OGC API Features/Tiles (pygeoapi), TiTiler |
| `spatial-processing/` | Agent 5 | FastAPI clip / zonal statistics / export |
| `demo-client/`, `tests/e2e/`, `docs/integration/` | Agent 6 | Viewer, end-to-end tests, integration report |

## Workflow

See `contracts/GIT_AND_MERGE_POLICY.md` and `contracts/RUN_ORDER.md`. All feature branches start from the
frozen `phase2/base` commit; only Agent 6 merges.

Startup instructions are added by the infrastructure branch.
