# `spatial-processing/` — clip, zonal statistics, GeoTIFF export (Agent 5)

FastAPI service exposed by the gateway under `/api/`. It resolves datasets from the PostGIS catalog,
clips rasters to GeoJSON AOIs, computes zonal statistics under a documented **pixel-center** rule, exports
GeoTIFF/COG with CRS, transform and NoData preserved, and records every operation in `ops.jobs`.
It is a custom API, **not** an OGC standard endpoint, and it performs no population modelling.

* API reference, conventions, error codes, guardrails: [`docs/API.md`](docs/API.md)
* OpenAPI snapshot: [`docs/openapi.json`](docs/openapi.json) (live at `/api/openapi.json`, UI at `/api/docs`)
* Compose / gateway / DB grants for Agent 1 & 6: [`docs/compose-request.md`](docs/compose-request.md)
* Contract interpretations and proposals: [`docs/contract-change-request.md`](docs/contract-change-request.md)
* Handoff: [`HANDOFF_REPORT.md`](HANDOFF_REPORT.md)

## Layout

```
src/spatial_processing/
  app.py        FastAPI app, routes, error handlers, body-size & root-path middleware
  service.py    validation → catalog → job → processing orchestration
  raster.py     pixel selection (all_touched=false), zonal stats, clip/COG writer
  geometry.py   AOI/CRS parsing & validation, AOI → raster CRS transform
  catalog.py    PostgresCatalog (catalog.current_datasets), InMemory/static catalogs
  jobs.py       PostgresJobStore (ops.jobs), InMemoryJobStore, status transitions
  outputs.py    per-job output dirs, atomic writes, safe TTL cleanup
  paths.py      asset path validation (root confinement, no URLs/VSI/symlink escape)
  units.py      count vs density unit semantics
  errors.py     structured error model and codes
  config.py     environment configuration
tests/          standalone tests (real synthetic GeoTIFFs + mocked catalog) and PostGIS tests
```

## Run tests

```bash
cd spatial-processing
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python -m pytest -q
# PostGIS integration tests (skipped without a database):
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/cgis .venv/bin/python -m pytest -q -m postgis
# or in Docker:
docker build --target test spatial-processing/
```

`pip install -e ".[test]"` in any Python ≥ 3.11 venv works too. Dependencies are pinned in `pyproject.toml`.

## Run locally without PostGIS

```bash
ASSET_ROOT=/path/to/assets OUTPUT_ROOT=/tmp/outputs ROOT_PATH=/api \
CATALOG_BACKEND=static STATIC_CATALOG_FILE=docs/static-catalog.example.json JOB_STORE_BACKEND=memory \
.venv/bin/uvicorn --factory spatial_processing.app:app_factory --port 8000
```

`ASSET_ROOT` must contain `synthetic_2001.tif` / `synthetic_2011.tif` from the ingestion fixture
generator (Agent 3). Production uses `CATALOG_BACKEND=postgres` and `JOB_STORE_BACKEND=postgres` with
`DATABASE_URL`.

Regenerate the OpenAPI snapshot after API changes:

```bash
.venv/bin/python -c "import json; from fastapi.testclient import TestClient; from spatial_processing.app import create_app; from spatial_processing.config import Settings; from pathlib import Path; c=TestClient(create_app(Settings(asset_root=Path('.'), output_root=Path('/tmp/sp-openapi'), catalog_backend='memory', job_store_backend='memory', root_path='/api'))); open('docs/openapi.json','w').write(json.dumps(c.get('/openapi.json').json(), indent=2)+'\n')"
```
