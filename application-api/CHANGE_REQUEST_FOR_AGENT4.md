# Change request: Agent 5 → Agent 4 (gateway, Compose, environment)

**From:** Agent 5 (`completion/agent5-application-api`). **To:** Agent 4 (owner of `compose.yaml`, `infra/`,
root `.env.example`, `ingestion/`, `database/`). **Copy:** Agent 7 (integration), Agent 6 (consumer).
**Status:** open. Agent 5 has **not** edited any Agent 4 path; everything below is delivered as ready-to-merge
files inside `application-api/` and was tested there.

## Summary

| # | Request | Delivered as | Tested |
|---|---|---|---|
| R1 | Gateway routes `/auth/`, `/admin/`, `/api/exports`, `/api/catalog/`, `/api/app/` → `app-api:8000`, **no prefix strip**; upload locations without the 1m cap and without request buffering; downloads without response buffering; `X-Real-IP` set at server level | `deploy/gateway/default.conf` = current `infra/gateway/default.conf` + one marked block + 1 upstream + 1 header line (diff below) | real nginx 1.31 + app + echo stubs: `tests/test_gateway.py` (routing split, 3+ MB upload through the gateway, service limit still 413, dot segments 400, read-only catalog, X-Real-IP not spoofable, Range download) |
| R2 | Services `app-volume-init`, `app-migrate`, `app-api`, `app-ingest-worker`, `app-export-worker`; volumes `app-staging`, `app-exports` | `deploy/compose.app-api.yaml` (overlay; `docker compose -f compose.yaml -f application-api/deploy/compose.app-api.yaml …`) | `docker compose … config --quiet` OK; container start **NOT_RUN** here (no Docker daemon access on this machine) |
| R3 | Root `.env.example`: add the `APP_*` block | `application-api/.env.example` + `application-api/scripts/init-env.sh` (appends with random secrets, never overwrites) | script tested (idempotent, keeps existing keys, mode 0600) |
| R4 | Decision on protecting read routes (`/ogc/`, `/tiles/`, `/api/operations`, `/api/downloads`) with `auth_request` | `deploy/gateway/default.protected.conf` (generated from R1 + `auth_request /_cgis_auth`) | `tests/test_gateway.py::test_protected_variant_gates_read_routes` |
| R5 | (optional) OGC/Tiles publication filtered by `cgis_app.dataset_access` (`admins` datasets not published) | — | — |
| R6 | Gateway re-resolution of upstream names (all routes, not only the app): nginx resolves `server name:port` once at start-up, so a recreated container with a new IP gets 502 until the gateway restarts | — | observed on the live stack after rebuilding `app-api`; `docker compose restart gateway` fixed it |

## R1 — gateway

Merge `application-api/deploy/gateway/default.conf` into `infra/gateway/default.conf`. Differences from the
current file (nothing else changes):

```nginx
upstream app_api_up    { server app-api:8000;    keepalive 16; }   # application API

# server level, next to the other proxy_set_header lines (a proxy_set_header inside a location would drop them):
proxy_set_header X-Real-IP $remote_addr;

location = /auth { return 301 $scheme://$http_host/auth/me; }
location ^~ /auth/            { proxy_pass http://app_api_up; client_max_body_size 16k; proxy_read_timeout 30s; }
location = /admin/datasets/uploads {
    proxy_pass http://app_api_up; client_max_body_size 8200m; proxy_request_buffering off;
    client_body_timeout 300s; proxy_send_timeout 3600s; proxy_read_timeout 3600s; }
location = /admin/datasets    { …same as /admin/datasets/uploads (GET list, POST upload alias)… }
location ^~ /admin/           { proxy_pass http://app_api_up; client_max_body_size 64k; proxy_read_timeout 60s; }
location = /api/exports       { proxy_pass http://app_api_up; client_max_body_size 1m; proxy_read_timeout 60s; }
location ^~ /api/exports/     { proxy_pass http://app_api_up; client_max_body_size 1m; proxy_buffering off;
                                proxy_read_timeout 3600s; }
location ^~ /api/catalog/     { limit_except GET { deny all; } proxy_pass http://app_api_up; }
location ^~ /api/app/         { limit_except GET { deny all; } proxy_pass http://app_api_up; }
```

* `proxy_pass` without a URI forwards the raw request URI, so the service sees the public paths. The existing
  global dot-segment refusal (`$dot_segment`) still applies first.
* `/api/operations/*` and `/api/downloads/*` keep going to `processing` (longest-prefix match), unchanged.
* `client_max_body_size 8200m` is the outer bound; the service enforces `APP_MAX_UPLOAD_BYTES` (default 8 GiB)
  itself while streaming. Keep the two in step if you change either.
* Backward compatibility: no existing route, header or limit changes. The gateway gains a dependency on
  `app-api` being healthy.

## R2 — Compose

Fold `deploy/compose.app-api.yaml` into `compose.yaml` (or keep it as an overlay and add it to the Makefile/
runbook). Key points:

* Build context is the **repository root** with `dockerfile: application-api/Dockerfile` and targets `api` /
  `ingest-worker`; `application-api/Dockerfile.dockerignore` limits the context. The `ingest-worker` target installs
  `ingestion/requirements.txt` and copies `ingestion/` unchanged (the importer is used as a library).
* `app-migrate` runs after `migrate` with the **owner** credentials: applies the `cgis_app` schema
  (`application-api/src/cgis_app/migrations/`) and creates login roles `APP_DB_USER` (group `cgis_app_api`, member
  of `cgis_reader`) and `APP_WORKER_DB_USER` (group `cgis_app_worker`, member of `cgis_ingest` + `cgis_reader`), then
  creates the bootstrap accounts. Idempotent; refuses placeholder/short passwords and reserved role names.
  Please add `cgis_app` to `PROJECT_SCHEMAS` in `infra/db/provision_roles.py` (a role named `cgis_app` would shadow
  the schema in `search_path`).
* Mounts (least privilege): `app-api` staging RW / assets RO / exports RO; `app-ingest-worker` staging RW /
  **assets RW**; `app-export-worker` assets RO / exports RW. No Docker socket anywhere.
* Memory limits sized from measurements (`docs/evidence/bench-*.json`): app-api 768m, ingest worker 2g (peak
  ≈ 590 MiB publishing a 576 M-cell raster), export worker 1536m (peak ≈ 360 MiB reprojecting it). Total added
  ≈ 4.3 GB limit, ≈ 1.2 GB typical, inside the 16 GB budget next to PostGIS/tiler.
* `app-volume-init` chowns the two new volumes to uid 10001 (same pattern as `volume-init`).

## R3 — environment

Append to the root `.env.example` (placeholders; `init-env.sh` fills `CHANGE_ME`):

```
APP_DB_USER=cgis_app_svc
APP_DB_PASSWORD=CHANGE_ME
APP_WORKER_DB_USER=cgis_app_worker_svc
APP_WORKER_DB_PASSWORD=CHANGE_ME
APP_BOOTSTRAP_ADMIN_USERNAME=cgis-admin
APP_BOOTSTRAP_ADMIN_PASSWORD=CHANGE_ME
APP_BOOTSTRAP_USER_USERNAME=cgis-viewer
APP_BOOTSTRAP_USER_PASSWORD=CHANGE_ME
APP_DEFAULT_DATASET_ACCESS=users
```

The full optional list is `application-api/.env.example`. Until then, operators run
`sh application-api/scripts/init-env.sh` after `sh infra/scripts/init-env.sh`.

## R4 — read-route protection (decision D-A5-1, needs Agent 7 / human)

`default.conf` keeps `/ogc/`, `/tiles/`, `/api/operations`, `/api/downloads` **public** (Phase 2 behaviour; CITE
runs anonymously; the existing e2e suite calls `/api/operations/clip` without credentials). The original aim says a
user "can only obtain permitted data", which argues for requiring sign-in there too. `default.protected.conf` adds:

```nginx
location = /_cgis_auth { internal; proxy_pass http://app_api_up/auth/verify?require=user;
    proxy_pass_request_body off; proxy_set_header Content-Length "";
    proxy_set_header X-Original-Method $request_method; proxy_set_header X-Original-URI $request_uri; … }
# in /ogc/, /tiles/, /api/:
auth_request /_cgis_auth; error_page 401 = @cgis_401; error_page 403 = @cgis_403;
```

With it, QGIS uses a personal access token (HTTP Basic, any user + token), CITE needs the same, and
cookie-session POSTs to `/api/operations/*` need `X-CSRF-Token` (verified in nginx's subrequest). Recommendation:
protected for any deployment with restricted data; public only for local demos/CITE.

## R5 — OGC publication of restricted datasets (optional)

`cgis_app.dataset_access(dataset_id, access)` holds per-dataset access (`public|users|admins`, default
`APP_DEFAULT_DATASET_ACCESS`). The OGC/Tiles publisher currently exposes every ready dataset. If restricted
sources (e.g. Bhuvan-derived layers) are published, either skip `access = 'admins'` in the publisher (read-only
`SELECT` on `cgis_app.dataset_access` for `cgis_reader` would be needed) or use R4.

## R6 — upstream re-resolution (observed on the live stack)

After rebuilding `app-api`, `docker compose up -d` recreated it with a new IP while the gateway kept the old one:
every `/auth`, `/admin`, `/api/exports` request returned 502 (`connect() failed … 172.19.0.5:8000`) until
`docker compose restart gateway`. The same applies today to `ogc`, `tiles`, `processing` and `viewer`. Options:
keep "restart the gateway after recreating a service" in the runbook (the Makefile `restart` target already does
`down` + `up`), or resolve at request time with Docker's DNS:

```nginx
resolver 127.0.0.11 valid=10s ipv6=off;
location ^~ /auth/ { set $app_api app-api:8000; proxy_pass http://$app_api; … }
```

(A `proxy_pass` with a variable and no URI part still forwards the original request URI; the `upstream` keepalive
pool is lost.) The overlay already sets `depends_on: app-api: {restart: true}` for the gateway, which covers
`docker compose restart app-api` but not a recreate.
