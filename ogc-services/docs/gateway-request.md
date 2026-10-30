# Gateway, Compose and environment request (Agent 4 → Agents 1 and 6)

Agent 4 does not edit the root `compose.yaml`, gateway config or `.env.example` (Agent 1 owns them).
This file lists exactly what the `ogc` and `tiles` services need. The test-only stack in
`ogc-services/tests/stack/` (`compose.yaml`, `gateway.nginx.conf`) implements these same settings and
can be copied.

## 1. Services

| Compose service | Build context | Listens on (inside the network) | Health check (already in the Dockerfile) |
|---|---|---|---|
| `ogc` | `ogc-services/pygeoapi` | `ogc:5000` (gunicorn, non-root) | `GET /conformance?f=json` → 200 |
| `tiles` | `ogc-services/tiler` | `tiles:8000` (uvicorn, non-root) | `GET /readyz` → 200 (DB reachable and asset volume mounted) |

Startup order: `db` healthy (with migrations applied) → `tiles` healthy → `ogc`. On start, `ogc` reads
`catalog.current_datasets` and generates its configuration (it waits up to `CGIS_OGC_DB_WAIT` seconds for
the database). It has no ordering dependency on `ingestion`: datasets registered later are picked up by
the refresh described in section 5.

```yaml
# Suggested root compose.yaml fragment (adapt names/secrets to Agent 1's conventions)
  tiles:
    build: {context: ./ogc-services/tiler}
    environment:
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${OGC_DB_USER:-${POSTGRES_USER}}
      POSTGRES_PASSWORD: ${OGC_DB_PASSWORD:-${POSTGRES_PASSWORD}}
      TILES_ROOT_PATH: /tiles
    volumes:
      - assets:/data/assets:ro
    depends_on:
      db: {condition: service_healthy}

  ogc:
    build: {context: ./ogc-services/pygeoapi}
    environment:
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${OGC_DB_USER:-${POSTGRES_USER}}
      POSTGRES_PASSWORD: ${OGC_DB_PASSWORD:-${POSTGRES_PASSWORD}}
      OGC_PUBLIC_URL: ${PUBLIC_BASE_URL:-http://localhost:8080}/ogc
      TILES_PUBLIC_URL: ${PUBLIC_BASE_URL:-http://localhost:8080}/tiles
      CGIS_TILER_INTERNAL_URL: http://tiles:8000
      OGC_REFRESH_INTERVAL: "30"
    depends_on:
      db: {condition: service_healthy}
      tiles: {condition: service_healthy}
```

## 2. Volumes

| Volume | Service | Mount | Mode |
|---|---|---|---|
| shared COG asset volume (written by `ingestion`) | `tiles` | `/data/assets` | **read-only** |
| — | `ogc` | none | `ogc` never reads raster files; it asks `tiles` by `dataset_id` |

`catalog.datasets.asset_path` must be relative to `/data/assets` (frozen contract). The tile service
also accepts an absolute path only if it resolves inside `/data/assets`.

## 3. Gateway routes

Both prefixes are **stripped** before proxying, and the original `Host` header must be forwarded.

| Public path (gateway `:8080`) | Upstream | Notes |
|---|---|---|
| `/ogc/…` | `http://ogc:5000/…` | OGC API landing page, conformance, collections, items, tiles, OpenAPI. Links are built from `OGC_PUBLIC_URL`, so it must equal the public URL of this prefix. |
| `/tiles/…` | `http://tiles:8000/…` | **Vendor-specific** TiTiler routes (TileJSON, `/info`, `/point`). Links are built from the forwarded `Host` plus `TILES_ROOT_PATH=/tiles`. |
| `/ogc` and `/tiles` (no slash) | `301` → with trailing slash | convenience |

nginx form (tested in `tests/stack/gateway.nginx.conf`):

```nginx
location /ogc/ {
    proxy_pass http://ogc:5000/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
location /tiles/ {
    proxy_pass http://tiles:8000/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

If the gateway cannot strip the `/ogc` prefix, set `SCRIPT_NAME=/ogc` on `ogc` instead (gunicorn then
removes it). Both services expose only GET/HEAD routes plus TiTiler's read-only `POST /statistics`; no
mutation endpoint exists, so no admin secret is required for these prefixes.

## 4. Environment variables

| Variable | Service | Default | Meaning |
|---|---|---|---|
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | both | `db`, `5432`, `cgis`, `cgis`, empty | Database connection. Only `SELECT` on `catalog.current_datasets`, `geo.admin_units` and `information_schema.columns` is needed — a read-only role is recommended (request to Agent 2). The password is passed by environment only; the generated pygeoapi config stores `${POSTGRES_PASSWORD}`, not the value. |
| `OGC_PUBLIC_URL` | ogc | `http://localhost:8080/ogc` | Public base URL of the OGC API (no trailing slash). |
| `TILES_PUBLIC_URL` | ogc | `http://localhost:8080/tiles` | Public base of the vendor tile routes, used for the "vendor TileJSON" link on raster collections. |
| `CGIS_TILER_INTERNAL_URL` | ogc | `http://tiles:8000` | Internal TiTiler address used by the OGC API - Tiles provider. Server-side only. |
| `OGC_REFRESH_INTERVAL` | ogc | `0` (off) | Seconds between automatic catalog re-publications (reload only when the catalog changed). `30` is suggested for the demo. |
| `CGIS_OGC_DB_WAIT` | ogc | `120` | Seconds to wait for the database at start-up. |
| `OGC_TILE_RESCALE` | ogc | unset | Shared `lo,hi` colour range for all OGC map tiles (e.g. `0,2` to compare 2001 vs 2011). Unset → per-dataset range. |
| `OGC_TILE_COLORMAP` | ogc | `viridis` | Colour ramp for OGC map tiles. |
| `OGC_TILE_SCHEMES` | ogc | `WebMercatorQuad,WorldCRS84Quad` | Published TileMatrixSets. |
| `OGC_VECTOR_SCHEMA`, `OGC_VECTOR_TABLE`, `OGC_VECTOR_ID_FIELD`, `OGC_VECTOR_GEOM_FIELD` | ogc | `geo`, `admin_units`, `unit_id`, `geom` | Contract vector table. |
| `WSGI_WORKERS` | ogc | `4` | gunicorn workers. |
| `TILES_ROOT_PATH` | tiles | empty | Set to `/tiles` behind the gateway. |
| `CGIS_ASSET_ROOT` | tiles | `/data/assets` | Asset volume mount point. |
| `CGIS_CATALOG_CACHE_TTL` | tiles | `30` | Seconds a dataset_id→file resolution is cached. |
| `WEB_CONCURRENCY` | tiles | `2` | uvicorn workers. |

No new variable is a secret apart from the existing database password.

## 5. Operational commands for the Makefile / runbook

```bash
# re-publish now (no hand-editing; reloads gunicorn only if the catalog changed)
docker compose exec ogc /cgis/entrypoint.sh refresh
# preview the generated config without applying it
docker compose exec ogc /venv/bin/python -m cgis_ogc.publish --dry-run
```

## 6. Smoke checks for Agent 6

```bash
curl -fsS http://localhost:8080/ogc/conformance?f=json
curl -fsS "http://localhost:8080/ogc/collections/synthetic-towns/items?f=json&bbox=78.009,17.979,78.021,17.991"
curl -fsS -o /tmp/t.png "http://localhost:8080/ogc/collections/synthetic-worldpop-2001/tiles/WebMercatorQuad/13/3680/5871?f=png"
curl -fsS http://localhost:8080/tiles/datasets/synthetic-worldpop-2001/WebMercatorQuad/tilejson.json
```

The full HTTP test suite and the OGC CITE suites can be pointed at the integrated stack; see
`ogc-services/README.md` → "Testing against the integrated deployment".
