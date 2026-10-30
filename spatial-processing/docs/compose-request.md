# Compose / gateway / environment request (Agent 5 → Agent 1 / Agent 6)

Agent 5 does not edit root `compose.yaml`, the gateway config or `.env.example`. Please wire the service as below.

## Service `processing`

```yaml
  processing:
    build:
      context: ./spatial-processing
      target: runtime
    environment:
      DATABASE_URL: postgresql://${PROCESSING_DB_USER}:${PROCESSING_DB_PASSWORD}@db:5432/${POSTGRES_DB}
      CATALOG_BACKEND: postgres
      JOB_STORE_BACKEND: postgres
      ASSET_ROOT: /data/assets
      OUTPUT_ROOT: /data/outputs
      ROOT_PATH: /api
      # optional guardrails (defaults in docs/API.md)
      MAX_CONCURRENT_JOBS: "4"
      OUTPUT_TTL_SECONDS: "86400"
    volumes:
      - assets:/data/assets:ro          # same named volume the ingestion service writes COGs to
      - processing-outputs:/data/outputs
    depends_on:
      db:
        condition: service_healthy
    healthcheck:                         # also baked into the image; /ready checks DB + storage
      test: ["CMD", "python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 20s

volumes:
  processing-outputs: {}
```

Notes:

* `DATABASE_URL` is a libpq conninfo. If unset, standard `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` env
  vars are used. Put the password only in `.env` (not committed); `.env.example` should carry placeholders.
* The assets volume is mounted **read-only**. Exports go to a separate writable volume owned by UID 10001
  (the image's non-root user). If a bind mount is used, `chown 10001` it or use a named volume.
* No Redis/worker is requested: operations on Phase 2 fixtures complete in milliseconds. See
  `contract-change-request.md` §5 for how a worker can be added later without an API change.

## Gateway route

Route `http://localhost:8080/api/` → `http://processing:8000/`. Either keep or strip the `/api` prefix;
the service routes both. Example nginx:

```nginx
location /api/ {
    proxy_pass http://processing:8000/;      # trailing slash strips /api
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 1m;                 # matches MAX_REQUEST_BYTES
    proxy_read_timeout 150s;                 # > MAX_JOB_SECONDS
}
```

Swagger UI is then at `http://localhost:8080/api/docs`, and the OpenAPI document at `/api/openapi.json`.

## Database privileges (for Agent 2 / Agent 6)

The processing role needs only:

```sql
GRANT USAGE ON SCHEMA catalog, ops TO processing;
GRANT SELECT ON catalog.datasets, catalog.current_datasets TO processing;
GRANT SELECT, INSERT, UPDATE ON ops.jobs TO processing;
```

No DDL is run by this service.

## Seeding / demo

After fixture import, the demo step "Town A clip, zonal sum = 100 for 2001" is:

```bash
curl -s -X POST localhost:8080/api/operations/zonal-statistics -H 'content-type: application/json' \
  -d '{"dataset_id":"synthetic-worldpop-2001","aoi_crs":"EPSG:4326","aoi":{"type":"Polygon","coordinates":[[[78.01,17.98],[78.02,17.98],[78.02,17.99],[78.01,17.99],[78.01,17.98]]]}}'
JOB=$(curl -s -X POST localhost:8080/api/operations/clip -H 'content-type: application/json' \
  -d '{"dataset_id":"synthetic-worldpop-2001","aoi_crs":"EPSG:4326","format":"GTiff","aoi":{"type":"Polygon","coordinates":[[[78.01,17.98],[78.02,17.98],[78.02,17.99],[78.01,17.99],[78.01,17.98]]]}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
curl -s -OJ localhost:8080/api/downloads/$JOB && gdalinfo -stats clip_*.tif
```

The Docker test stage (`docker build --target test spatial-processing/`) runs the standalone suite.
