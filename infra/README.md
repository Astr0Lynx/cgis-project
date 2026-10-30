# Infrastructure: Docker Compose stack, gateway and task commands

Owner: Agent 1. Everything here runs locally with Docker Compose. No cloud services, Kubernetes or
paid accounts are involved, and all data is **synthetic** until Phase 1 outputs exist.

## Machine requirements

| Item | Minimum | Notes |
|---|---|---|
| Docker Engine | 24+ | Docker Desktop 4.30+ on Windows/macOS. Tested with Docker Desktop (Engine 29.1.3, Compose v5.0.0) on Windows 11. |
| Docker Compose | v2.24+ | Needs `dockerfile_inline`, `additional_contexts` and `--wait`; the mock routing test also needs `!reset`/`!override`. |
| RAM for Docker | 4 GB | 8 GB is comfortable while images build. |
| Disk | ~6 GB | Images plus volumes; the synthetic fixtures are tiny. |
| Host tools | `curl`, a POSIX `sh` | Needed only for the smoke and test scripts. On Windows, use Git Bash. `make` is optional. |

**Windows:** Docker Desktop with the WSL 2 backend. The repository's `.gitattributes` forces LF line
endings, so shell entrypoints copied into Linux images still work when `core.autocrlf=true`. If you
cloned before `.gitattributes` existed, re-clone, or run `git rm --cached -r . && git reset --hard`
in a clean tree.

**Linux with SELinux (Fedora/RHEL, podman):** add `:z` to the `./data/fixtures` bind mounts.

## Services

| Service | Build / image | Role | Ports |
|---|---|---|---|
| `volume-init` | `busybox:1.37.0` (pinned digest) | One-shot. Gives the `assets` and `processing-outputs` volumes to uid 10001 and makes `./data/fixtures` writable. | none |
| `db` | `postgis/postgis:17-3.5` (pinned digest) | PostgreSQL 17 + PostGIS 3.5. Data in the `pgdata` volume. | **none** (private) |
| `migrate` | `infra/migrate/Dockerfile` (context: repo root) | One-shot. `python -m cgis_db migrate up` (Agent 2), then `infra/db/provision_roles.py`. | none |
| `ingestion` | `ingestion/` (Agent 3) | Tool container in the `tools` profile, so `up` does not start it. Fixture generator and importer. Only writer of `assets`. | none |
| `tiles` | `ogc-services/tiler/` (Agent 4) | Restricted TiTiler, vendor-specific raster routes. Mounts `assets` read-only. | none |
| `ogc` | `ogc-services/pygeoapi/` (Agent 4) | pygeoapi: OGC API Features and Tiles, generated from the catalog. | none |
| `processing` | `spatial-processing/` (Agent 5) | FastAPI clip, zonal statistics, jobs and downloads. Mounts `assets` read-only; writes `processing-outputs`. | none |
| `viewer` | `demo-client/` (Agent 6) + `infra/viewer/default.conf` | Static diagnostic viewer. | none |
| `gateway` | `infra/gateway/` (nginx 1.27, pinned digest) | Single entry point. | `127.0.0.1:8080` |

Start order is enforced with `depends_on` conditions: `volume-init` (completed) and `db` (healthy) →
`migrate` (completed) → `tiles` (healthy) → `ogc` (healthy); `processing` and `viewer` (healthy) →
`gateway`. A service that needs the schema never starts before migrations have finished.

Health checks test readiness, not just that a process is running:

- `db` uses TCP `pg_isready`. The image's temporary init server listens only on the Unix socket, so
  the check does not pass during `initdb`.
- `tiles` uses `/readyz` (DB reachable, asset volume mounted).
- `ogc` uses `/conformance`.
- `processing` uses `/ready` (DB and storage).
- `gateway` and `viewer` use `/healthz`.

## Public routes (gateway)

| Path | Upstream | Methods | Notes |
|---|---|---|---|
| `/` | — | GET | 302 to `/viewer/` |
| `/viewer/` | `viewer:8080` | GET/HEAD | Static diagnostic viewer |
| `/ogc/` | `ogc:5000` | GET/HEAD | OGC API landing, conformance, collections, items, tiles, OpenAPI |
| `/tiles/` | `tiles:8000` | GET/HEAD | **Vendor-specific** TiTiler routes, not an OGC API |
| `/api/` | `processing:8000` | all | Custom processing API (`/api/docs`, `/api/openapi.json`); 1 MiB body limit |
| `/healthz` | — | GET | Gateway liveness only |
| anything else | — | — | JSON 404 |

Routing rules:

- **Prefix stripping.** The prefix is removed before the request is forwarded. The upstream path
  comes from the raw request URI, so percent-encoding and the query string arrive unchanged.
- **Host header.** The client's `Host` is forwarded, so upstreams build links back through the
  gateway. `PUBLIC_BASE_URL` must match how clients reach the gateway, because OGC links are built
  from it.
- **Redirects.** They are relative, so they work on any host port.
- **Refused requests.** Dot-segment paths (`/../`, `%2e%2e`) get a 400. Writes to the read-only
  prefixes get a 403.
- **Logs.** Access logs are JSON lines on stdout. They omit query strings.

## Environment

Compose reads `.env` from the repository root automatically. `.env` is git-ignored and must never be
committed. Create it in one of these ways:

```sh
sh infra/scripts/init-env.sh   # copies .env.example and replaces every CHANGE_ME with random hex
# or: cp .env.example .env, then edit each CHANGE_ME by hand
```

Required variables have no defaults: Compose stops with `set X in .env` if one is missing.

The roles are split like this:

- **Owner.** `POSTGRES_USER`/`POSTGRES_PASSWORD` are used only by `db` and `migrate`.
- **Services.** Each service connects with its own login role, which `migrate` creates as a member of
  one of Agent 2's group roles:

  | Variables | Group role | Privileges |
  |---|---|---|
  | `INGEST_DB_*` | `cgis_ingest` | read and write catalog and geo |
  | `OGC_DB_*` (also used by `tiles`) | `cgis_reader` | read-only, with read-only sessions |
  | `PROCESSING_DB_*` | `cgis_processing` | read catalog; read and write `ops.jobs` |

`provision_roles.py` refuses empty, short (under 12 characters) or `CHANGE_ME` passwords. It
re-applies the passwords on every run, so changing one in `.env` takes effect on the next `up`.

## Commands

`make` is optional: every target is a thin wrapper around `docker compose`.

| Task | make | Raw command |
|---|---|---|
| Create `.env` | `make env` | `sh infra/scripts/init-env.sh` |
| Validate config | `make config` | `docker compose config --quiet` |
| Build everything | `make build` | `docker compose --profile tools build` |
| Start, wait for healthy | `make up` | `docker compose up -d --build --wait` |
| Status | `make ps` | `docker compose ps -a` |
| Logs | `make logs SERVICE=ogc` | `docker compose logs -f --tail=200 ogc` |
| Migrations + roles | `make migrate` | `docker compose run --rm migrate` |
| Generate SYNTHETIC fixtures | `make fixtures` | `docker compose run --rm ingestion generate-fixtures --out-dir /data/fixtures` |
| Import manifest | `make import` | `docker compose run --rm ingestion import --manifest /data/fixtures/manifest.json` |
| Generate + import | `make seed` | `docker compose run --rm ingestion seed` |
| Route smoke test | `make smoke` | `sh infra/scripts/smoke.sh http://localhost:8080` |
| Restart, keep data | `make restart` | `docker compose restart` |
| Stop, keep data | `make down` | `docker compose down` |
| **Delete all data** | `make reset CONFIRM=1` | `docker compose down -v` |

`make check-modules` (run by `build` and `up`) fails with a clear message if a module Dockerfile or
`.env` is missing.

Development database port (opt-in, loopback only):
`docker compose -f compose.yaml -f infra/compose.db-port.yaml up -d db` publishes
`127.0.0.1:${DB_HOST_PORT:-55432}`.

## Volumes

| Volume | Mounted by | Contents |
|---|---|---|
| `pgdata` | `db` | PostgreSQL cluster (catalog, geo, ops) |
| `assets` | `ingestion` (rw); `tiles`, `processing` (ro) | COG rasters and GeoJSON assets under `/data/assets` |
| `processing-outputs` | `processing` | Clip/export results under `/data/outputs` |
| `./data/fixtures` (bind) | `ingestion`, `volume-init` | Generated SYNTHETIC fixtures (git-ignored), handy for QGIS/`gdalinfo` on the host |

`docker compose down` and `restart` keep the volumes. Only `down -v` (`make reset CONFIRM=1`)
deletes them.

## Infrastructure tests

Run these from the repository root.

```sh
docker compose config --quiet                       # config resolves with .env values
sh infra/tests/test_db_persistence.sh               # db healthy; data survives down/up without -v
# gateway routing against stand-in upstreams (no service modules needed):
docker compose -p cgis-gateway-test -f compose.yaml -f infra/tests/compose.mock.yaml up -d --build --wait gateway
sh infra/tests/test_gateway_routes.sh
sh infra/scripts/smoke.sh http://localhost:18080
docker compose -p cgis-gateway-test -f compose.yaml -f infra/tests/compose.mock.yaml down -v
```

Always pass `-p` for test stacks: `COMPOSE_PROJECT_NAME` in `.env` takes precedence over a file's
`name:`, so without `-p` a test would reuse the real stack's volumes.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `required variable POSTGRES_PASSWORD is missing` | No `.env`; run `sh infra/scripts/init-env.sh`. |
| `migrate` exits 1 with `provision_roles: ERROR` | A service password is still `CHANGE_ME`, too short, or a user name clashes. Fix `.env` and run `docker compose up -d` again. |
| `exec /cgis/entrypoint.sh: no such file or directory` | CRLF line endings from an old Windows checkout; see Windows above. |
| `fixture_write_failed` | `./data/fixtures` is not writable by uid 10001; `volume-init` normally fixes this. On SELinux hosts, add `:z`. |
| Port 8080 already in use | Set `GATEWAY_PORT` (and `PUBLIC_BASE_URL`) in `.env`. |
| Gateway returns 502 after recreating one backend | nginx resolves upstream names at start-up; run `docker compose restart gateway`. |
| Password changed in `.env` but `db` refuses it | The owner password is set only when `pgdata` is first created. Change it with `ALTER ROLE`, or reset the volumes. Service passwords are re-applied automatically. |
