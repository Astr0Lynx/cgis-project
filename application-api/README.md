# application-api: accounts, admin dataset hosting, catalog discovery and exports

Owner: Agent 5, branch `completion/agent5-application-api`. This module implements the application layer
of the original flowchart: **an admin can host/upload datasets, a user can only obtain permitted data**,
and anyone signed in can **download a dataset (full or clipped) in a chosen CRS and geo-format**.

It is a narrow FastAPI service plus two workers. It reuses the existing code instead of replacing it:

* **Publication** goes through the existing importer (`ingestion/importer.py`, unchanged) running in its own
  worker on the ingestion image's pinned GDAL stack. The importer owns validation, COG conversion,
  catalog writes and the immutability of ready versions.
* **Clips** reuse the tested `spatial-processing` code (pixel-centre rule, NoData kept).
* **Discovery** reads the frozen `catalog.datasets` / `catalog.current_datasets` contract.
* `/api/operations/*` and `/api/downloads/*` of the processing service are untouched.

API reference: [`docs/API.md`](docs/API.md). OpenAPI 3.1: [`docs/openapi.json`](docs/openapi.json) (also served
live at `/api/app/openapi.json` and `/api/app/docs`). Security design and review notes:
[`docs/SECURITY.md`](docs/SECURITY.md). QGIS/GDAL access: [`docs/QGIS.md`](docs/QGIS.md). Recorded request/response
pairs from a real run: [`docs/examples/`](docs/examples/). Handoff: [`HANDOFF_REPORT.md`](HANDOFF_REPORT.md).

## Routes (through the gateway, no prefix stripping)

| Path | Who | Purpose |
|---|---|---|
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | anyone | browser cookie session (+CSRF token) or bearer token; `/auth/me` is 200 for anonymous callers |
| `POST/GET/DELETE /auth/tokens` | signed in | personal access tokens for QGIS/GDAL/scripts |
| `POST /auth/password` | signed in | change own password (revokes other sessions and tokens) |
| `GET /auth/verify` | gateway | nginx `auth_request` target (204/401/403) |
| `/admin/datasets…`, `/admin/jobs…` | **admin** | list, upload, validate, publish, discard, versions, access policy, job progress |
| `GET /api/catalog/datasets…` | per dataset access level | path-free metadata: identity, version, kind, year, level, CRS, bbox, resolution, units, NoData, provenance/license |
| `GET /api/exports/capabilities` | anyone | formats per kind, CRS rules, limits |
| `/api/exports…` | **user/admin** | create export, status/progress, cancel, download (Range), metadata |
| `GET /api/app/health`, `/ready`, `/openapi.json`, `/docs` | anyone | service |

## Components

```
            gateway (nginx, Agent 4)  ── /auth /admin /api/exports /api/catalog /api/app ──▶ app-api (FastAPI)
                                                                                          │  staging RW, assets RO,
                                                                                          │  exports RO; DB role
                                                                                          │  cgis_app_api
                         cgis_app.jobs (PostgreSQL queue, leases, SKIP LOCKED)  ◀─────────┘
                           │ queue=ingest                       │ queue=export
                 app-ingest-worker                     app-export-worker
                 existing importer (ingestion/)        WarpedVRT strips / processing clip / GeoJSON
                 staging RW, assets RW                 assets RO, exports RW
                 DB role cgis_app_worker (→ cgis_ingest)  DB role cgis_app_worker
```

No component mounts the Docker socket or runs Docker commands. Uploads never name a server path or URL.

## Run it

```sh
sh infra/scripts/init-env.sh                 # root .env (Agent 4) if you do not have one
sh application-api/scripts/init-env.sh       # adds APP_* settings with random secrets; never overwrites
docker compose -f compose.yaml -f application-api/deploy/compose.app-api.yaml --profile tools build
docker compose -f compose.yaml -f application-api/deploy/compose.app-api.yaml up -d --wait
grep APP_BOOTSTRAP_ADMIN .env                # the bootstrap admin account (change it after first sign-in)
```

After rebuilding the application images (`… build app-api app-ingest-worker && … up -d --wait`), run
`docker compose restart gateway`: nginx keeps the old container address otherwise (502 on the app routes).

`deploy/compose.app-api.yaml` is an **overlay**: it adds `app-volume-init`, `app-migrate` (schema, login roles,
bootstrap accounts), `app-api`, `app-ingest-worker`, `app-export-worker` and two volumes, and mounts the
proposed gateway config. Agent 4 owns `compose.yaml`/`infra/`; folding this in is requested in
[`CHANGE_REQUEST_FOR_AGENT4.md`](CHANGE_REQUEST_FOR_AGENT4.md).

Accounts:

```sh
docker compose run --rm app-api cgis-app users list
docker compose run --rm -T app-api cgis-app users add alice --role user --password-stdin < pw.txt
docker compose run --rm app-api cgis-app users passwd alice           # prompts; never on the command line
docker compose run --rm app-api cgis-app users disable alice          # revokes sessions and tokens
docker compose run --rm app-api cgis-app fixtures --out /tmp/e2e.json # random-password e2e-admin/e2e-user
```

## Configuration

All settings are `APP_*` environment variables (`src/cgis_app/config.py`); [`.env.example`](.env.example) lists
the ones a deployment normally sets. Secrets can also come from `NAME_FILE` (Compose secrets). There is no
default password anywhere; `cgis-app migrate` refuses placeholder or short role passwords and account
passwords must pass a strength check.

| Variable | Default | Meaning |
|---|---|---|
| `APP_DEFAULT_DATASET_ACCESS` | `users` | access level of datasets without an explicit policy (`public` = anonymous discovery, demo only) |
| `APP_SESSION_TTL_SECONDS` / `APP_SESSION_IDLE_SECONDS` | 28800 / 3600 | absolute and idle session lifetime |
| `APP_MAX_UPLOAD_BYTES` / `APP_MAX_VECTOR_UPLOAD_BYTES` | 8 GiB / 256 MiB | per-file upload limits (GeoJSON is parsed in memory by the importer) |
| `APP_STAGING_MAX_TOTAL_BYTES` / `APP_MIN_FREE_BYTES` | 32 GiB / 2 GiB | staging quota and free-space floor (507 when exceeded) |
| `APP_MAX_RASTER_PIXELS` / `APP_MAX_RASTER_UNCOMPRESSED_BYTES` / `APP_MAX_COMPRESSION_RATIO` | 3e9 / 16 GiB / 2000 | decompression-bomb guards checked from the TIFF header |
| `APP_EXPORT_MAX_PIXELS` / `APP_EXPORT_CLIP_MAX_PIXELS` | 3e9 / 25e6 | reprojected grid and clip window limits |
| `APP_EXPORT_MAX_OUTPUT_BYTES` / `APP_EXPORT_MAX_TOTAL_BYTES` / `APP_EXPORT_TTL_SECONDS` | 8 GiB / 32 GiB / 86400 | export size, volume quota, retention |
| `APP_EXPORT_MAX_SECONDS` / `APP_JOB_MAX_ATTEMPTS` / `APP_JOB_LEASE_SECONDS` | 3600 / 3 / 120 | job time limit and crash recovery |
| `APP_GDAL_CACHEMAX_MB` | 256 | GDAL block cache per worker process |

## Tests

```sh
# one-time: a PostGIS 14+ server binary dir, an API venv and an ingestion venv
pip install -e "application-api[test]" ./database ./spatial-processing         # API interpreter
pip install -r ingestion/requirements-dev.txt                                   # ingestion interpreter
sh application-api/scripts/fetch_real_samples.sh                                # optional real inputs (24 MB)

PG_BIN=/path/to/pg/bin PYTHON=/path/to/api-venv/bin/python \
APP_TEST_INGEST_PYTHON=/path/to/ingest-venv/bin/python APP_TEST_NGINX=/path/to/nginx \
  sh application-api/scripts/run_tests.sh -q
```

`run_tests.sh` starts a throwaway PostGIS cluster, applies the real `database/` migrations and this module's
migrations, and connects the API and workers with their own least-privilege roles. The ingest worker runs
**out of process on the ingestion pins**, like the `ingest-worker` image. Suites: unit (no DB), auth,
authorization matrix, admin lifecycle, exports, gateway (real nginx with the proposed config), real data
(WorldPop 1 km India 2011, geoBoundaries ADM1) and a bounded-memory benchmark (`APP_BENCH=1`).
`docker build -f application-api/Dockerfile --target test .` runs the unit tests in the image.
