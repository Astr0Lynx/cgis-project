# `ogc-services/` — OGC API publication and raster tiles (Agent 4)

**Owner:** Agent 4 — OGC services (sole initial owner per `contracts/ARCHITECTURE.md`).

Publishes datasets registered in the PostGIS catalog through standard interfaces:

| Service | Image | Role |
|---|---|---|
| `ogc` | `pygeoapi/` (pygeoapi 0.24.0 + `cgis_ogc`) | **OGC API - Features** for vector datasets and **OGC API - Tiles** (PNG map tiles) for raster datasets, under gateway `/ogc/` |
| `tiles` | `tiler/` (titiler.core 2.4.0, restricted) | **Vendor-specific** TiTiler routes (TileJSON, `/info`, `/point`) under gateway `/tiles/`; also the renderer behind the OGC tiles |

Phase 2 runs on deterministic **SYNTHETIC** fixtures (`contracts/SYNTHETIC_DATA.md`); no value served
here is real population data.

## How it works

```
catalog.current_datasets ──(python -m cgis_ogc.publish)──► generated pygeoapi config ──► ogc (gunicorn)
        │                                                                                  │ OGC API - Tiles provider
        │ dataset_id → asset_path (ready rasters only)                                     ▼ GET http://tiles:8000/datasets/{id}/tiles/...
        └──────────────────────────────────────────────────────────────────────────────► tiles ──► /data/assets/<asset_path> (read-only)
```

- **Catalog-driven publication** — `cgis_ogc/publish.py` turns every `ready` row of
  `catalog.current_datasets` into a collection (`collectionId` = `dataset_id`). No dataset is listed by
  hand; `OGC_REFRESH_INTERVAL` or `/cgis/entrypoint.sh refresh` re-publishes and reloads gunicorn only
  when the catalog changed.
- **Standards adapter** — `cgis_ogc/conformance.py` corrects pygeoapi 0.24.0's over-broad conformance
  declaration and a few protocol details, and refuses to start on configurations where the corrected
  declaration would become untrue. See `docs/conformance-matrix.md` §4.
- **Restricted tiles** — the tile service has no `url=` parameter and no `/cog` endpoint. A raster is
  addressed only by `dataset_id`, looked up in the catalog (ready rasters only), confined to
  `/data/assets` after symlink resolution, and must open as a GeoTIFF (`tiler/cgis_tiler/assets.py`).

## Layout

```
pygeoapi/                 ogc image
  Dockerfile, docker/entrypoint.sh, config/pygeoapi.base.yml
  cgis_ogc/publish.py     catalog -> pygeoapi config (+ OpenAPI)
  cgis_ogc/conformance.py standards adapter (pygeoapi 0.24.0 only)
  cgis_ogc/pygeoapi_plugins/postgresql.py    feature provider scoped to one dataset
  cgis_ogc/pygeoapi_plugins/titiler_tiles.py OGC API - Tiles provider backed by TiTiler
  cgis_ogc/tms.py         TileMatrixSet limit arithmetic
tiler/                    tiles image (Dockerfile, requirements.txt + requirements.lock, cgis_tiler/)
tests/unit/               path safety, tile maths (vs morecantile), publisher
tests/unit_pygeoapi/      adapter and providers inside the real pygeoapi image
tests/integration/        external HTTP through the gateway stub
tests/stack/              TEST-ONLY compose stack: stub catalog schema, stub fixtures, gateway stub
tests/cite/               OGC CITE TEAM Engine runner
tests/qgis/               headless QGIS check
scripts/test-all.sh       everything above in one command
docs/                     gateway-request.md, conformance-matrix.md, usage.md, evidence/
```

## Run the tests

Requires Docker with Compose v2. From the repository root:

```bash
ogc-services/scripts/test-all.sh          # build, stack up, all suites, CITE, stack down
```

or step by step:

```bash
S=ogc-services/tests/stack/compose.yaml
docker compose -f $S up -d --build --wait
docker compose -f $S run --rm tests                                 # unit + integration (pytest)
docker run --rm -v "$PWD/ogc-services/tests:/cgis/tests:ro" --entrypoint /venv/bin/python \
  cgis-ogc:dev -m pytest -q -p no:cacheprovider /cgis/tests/unit_pygeoapi   # adapter unit tests
ogc-services/tests/cite/run_cite.sh                                 # OGC CITE Features + Tiles
docker compose -f $S down -v
```

The stack publishes its gateway stub on `127.0.0.1:18080` (`CGIS_TEST_GATEWAY_PORT`).

### Testing against the integrated deployment

The integration tests and the CITE runner take their targets from the environment, so Agent 6 can point
them at the real stack. The tests create and delete temporary `itest-*` catalog rows, so they need a DB
role with INSERT/DELETE on `catalog.datasets`, and the publication tests expect `OGC_REFRESH_INTERVAL`
(e.g. `5`) on `ogc`:

```bash
docker run --rm --network <project network> -v "$PWD/ogc-services:/work:ro" \
  -e CGIS_GATEWAY_URL=http://gateway:8080 -e POSTGRES_HOST=db -e POSTGRES_DB=... -e POSTGRES_USER=... \
  -e POSTGRES_PASSWORD=... cgis-ogc-tests:dev python -m pytest -q tests/integration
CGIS_TEST_NETWORK=<project network> CGIS_CITE_IUT=http://gateway:8080/ogc/ ogc-services/tests/cite/run_cite.sh
```

(`cgis-ogc-tests:dev` is built from `ogc-services/tests/Dockerfile` with context `ogc-services/`.)

## Integration

Routes, environment variables and volumes requested from Agents 1/6: `docs/gateway-request.md`.
Contract change proposals: `docs/contract-change-requests/agent4.md` (repository root `docs/`).
curl and QGIS instructions: `docs/usage.md`. Handoff: `HANDOFF_REPORT.md`.
