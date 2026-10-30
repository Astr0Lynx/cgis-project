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

## Quick start

Requires Docker with Compose v2.24+ (Docker Desktop on Windows/macOS). On Windows, run the `sh`
scripts in Git Bash. Full details, raw commands and troubleshooting: [`infra/README.md`](infra/README.md).

```sh
sh infra/scripts/init-env.sh              # create .env with random local passwords (never committed)
docker compose up -d --build --wait       # build and start; waits for health checks (make up)
docker compose run --rm ingestion seed    # generate + import the SYNTHETIC fixtures (make seed)
sh infra/scripts/smoke.sh                 # every gateway route reaches a ready service (make smoke)
```

Everything is served through one gateway on `http://localhost:8080`:

| Path | Service |
|---|---|
| `/viewer/` | Diagnostic map viewer |
| `/ogc/` | OGC API: Features and Tiles (pygeoapi) |
| `/tiles/` | TiTiler raster routes (vendor-specific, not an OGC API) |
| `/api/` | Processing API: clip, zonal statistics, jobs, downloads (`/api/docs`) |

Stop with `docker compose down`; this keeps the data. `docker compose down -v` deletes all volumes.
