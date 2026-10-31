# Runbook: CyberGIS Phase 2 platform

This runbook covers reproducing the stack from a fresh clone, running the acceptance tests,
operating the platform, and loading real Phase 1 outputs later. Everything runs locally under
Docker Compose. All data in the repository is **SYNTHETIC**.

## 1. Requirements

- **Docker:** Engine 24+ with Compose v2.24+. On Windows/macOS use Docker Desktop; on Windows use
  the WSL 2 backend.
- **Resources:** about 4–8 GB RAM for Docker, and about 6 GB of disk for images. The optional
  CITE, QGIS and Playwright checks pull roughly 4 GB more.
- **Host tools:** `git`, `curl` and a POSIX `sh`. On Windows, use Git Bash. `make` is optional, and
  host GDAL is optional.
- **Network:** internet access to pull images (pinned by digest). The viewer page also loads
  MapLibre from jsDelivr.

## 2. Fresh-clone setup

```sh
git clone https://github.com/Astr0Lynx/cgis-project.git && cd cgis-project
git switch main            # or phase2/integration
sh infra/scripts/init-env.sh             # .env with random local passwords; never commit it
# optional: edit .env -> GATEWAY_PORT / PUBLIC_BASE_URL if port 8080 is taken
docker compose --profile tools build     # all images, including the ingestion tool
docker compose up -d --wait              # db -> migrate (+ roles) -> tiles -> ogc / processing / viewer -> gateway
docker compose run --rm ingestion seed   # generate + import SYNTHETIC fixtures (idempotent)
sh infra/scripts/smoke.sh                # all gateway routes reach a ready service
```

Then open `http://localhost:8080/viewer/`, or the port set in `.env`.

| URL | What |
|---|---|
| `/viewer/` | Diagnostic map viewer |
| `/ogc/` | OGC API landing page. Also `/ogc/conformance`, `/ogc/collections`, `/ogc/openapi`. |
| `/ogc/collections/synthetic-towns/items` | OGC API - Features |
| `/ogc/collections/synthetic-worldpop-2001/tiles` | OGC API - Tiles |
| `/ogc/TileMatrixSets` | Tile matrix set definitions |
| `/tiles/datasets` | TiTiler, **vendor-specific** |
| `/api/docs` | Processing API (Swagger UI). Also `/api/openapi.json`. |

## 3. Acceptance tests

```sh
sh tests/e2e/run_e2e.sh --browser             # full sequence incl. restart persistence + headless viewer
sh tests/e2e/run_module_suites.sh --cite --qgis   # module owners' suites + OGC CITE + headless QGIS
```

Both scripts exit 0 only if every step passed. Evidence goes to `./data/fixtures/e2e/` and
`./data/modtest-evidence/`, both git-ignored. See `tests/e2e/README.md` for the step list.

## 4. Operations

| Task | Command |
|---|---|
| Status / health | `docker compose ps -a` |
| Logs | `docker compose logs -f --tail=200 <service>`. The gateway logs JSON lines. |
| Restart, keeping data | `docker compose restart`, then `docker compose up -d --wait` |
| Stop, keeping data | `docker compose down` |
| Re-run migrations and roles | `docker compose run --rm migrate` (idempotent) |
| Re-publish the OGC catalog now | `docker compose exec ogc /cgis/entrypoint.sh refresh` (otherwise every `OGC_REFRESH_INTERVAL` s) |
| Development DB port | `docker compose -f compose.yaml -f infra/compose.db-port.yaml up -d db` (127.0.0.1:55432) |
| **Delete everything** | `docker compose down -v` |

Data lives in three named volumes: `pgdata`, `assets` and `processing-outputs`. `docker compose
down` and `restart` keep them. The e2e suite checks that the catalog, features, tiles, statistics
and an earlier export (same SHA-256) survive a restart.

## 5. Loading real Phase 1 outputs (no code changes)

Real data goes through the **same importer**. The full acceptance rules are in
[`ingestion/docs/PHASE1_HANDOFF.md`](../../ingestion/docs/PHASE1_HANDOFF.md), and a template is in
`ingestion/examples/manifest.phase1-template.json`.

1. Put the delivery under `./data/incoming/<delivery>/`, with `manifest.json`, `rasters/` and
   `vectors/`. This directory is mounted read-only in the `ingestion` tool container.
2. Give each dataset a stable `dataset_id` and `version: 1`. Set `product_level: source` for
   processed WorldPop. Use `units: people_per_cell` for counts, or an explicit density unit.
   Include `checksum_sha256`, `expect` and full `provenance`.
3. Validate, then import:
   ```sh
   MSYS_NO_PATHCONV=1 docker compose run --rm ingestion validate --manifest /data/incoming/<delivery>/manifest.json
   MSYS_NO_PATHCONV=1 docker compose run --rm ingestion import   --manifest /data/incoming/<delivery>/manifest.json
   ```
4. The dataset becomes `ready` only after validation and the COG round-trip check. It then appears
   in `/ogc/collections` within `OGC_REFRESH_INTERVAL` seconds and is usable in `/api/operations/*`.
   Nothing in the services is keyed on the synthetic ids.
5. **Changed a file?** Import it as `version: 2`. The higher ready version becomes current, and
   version 1 is kept. A re-import with the same id, version and checksum is a no-op. A different
   checksum under the same version is refused (`checksum_conflict`).

Phase 3 outputs (Level 1 ≈ 100 m, Level 2 ≈ 23.5 m) use the same manifest with
`product_level: level1` or `level2`. No schema or API change is needed.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `required variable … is missing` | No `.env`; run `sh infra/scripts/init-env.sh`. |
| `ports are not available … 8080` | Another program uses 8080. On the integration machine it was a local GeoServer. Set `GATEWAY_PORT=8088` and `PUBLIC_BASE_URL=http://localhost:8088` in `.env`. |
| `migrate` exits 1 | Read `docker compose logs migrate`: a placeholder or short password, or an owner name equal to a schema name (use `cgis_admin`). |
| `processing` restarting with `libexpat.so.1` | An old image; rebuild with `docker compose --profile tools build`. |
| `seed` behaves like old code | `up --build` does not rebuild the tool image; run `docker compose --profile tools build`. |
| In Git Bash, `/data/...` arguments turn into `C:/Program Files/Git/data/...` | Prefix the command with `MSYS_NO_PATHCONV=1`. |
| `exec … entrypoint.sh: no such file` | A CRLF checkout from before `.gitattributes`; re-clone. |
| Gateway 502 after recreating one service | `docker compose restart gateway` (nginx resolves upstreams at start). |
| OGC tiles return 404 above zoom 13 | Correct behaviour: the tileset's `tileMatrixSetLimits` end at the fixture's native resolution. Clients should over-zoom, as the viewer does. |
| More | See [`infra/README.md`](../../infra/README.md) § Troubleshooting. |
