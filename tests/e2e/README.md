# `tests/e2e/`: end-to-end acceptance tests

**Owner:** Agent 6 (integration).

These tests run against a real Docker Compose deployment. A client-side test talks to the stack
only through the public gateway, as a user would. The tests read PostgreSQL directly only to
inspect the catalog, in a read-only session. Expected values come from
`contracts/SYNTHETIC_DATA.md`.

## Commands (from the repository root, with `.env` present)

```sh
sh tests/e2e/run_e2e.sh              # full acceptance run (see below); exit 0 = all steps passed
sh tests/e2e/run_e2e.sh --browser    # also drive the viewer in headless Chromium (~2 GB image)
sh tests/e2e/run_module_suites.sh --cite --qgis   # module owners' suites + OGC CITE + headless QGIS
```

`run_e2e.sh` performs these steps in order. It never deletes volumes.

1. `docker compose up -d --build --wait`, then records the start-up time.
2. Seeds twice (`docker compose run --rm ingestion seed`). The second run must report `unchanged`.
3. Writes bad inputs (`make_negative_fixtures.py`) and imports each one with the real ingestion
   CLI: a corrupt TIFF, a raster with no CRS, a non-EPSG CRS, a conflicting re-import, a bowtie
   polygon and a missing file.
4. Runs pytest inside the Compose network (`compose.e2e.yaml`, service `e2e`) with
   `-m "not persistence"`.
5. Runs `gdalinfo -stats` on the exported Town A clip, using the host GDAL or the
   `osgeo/gdal` image.
6. *(with `--browser`)* Runs the headless-Chromium viewer check.
7. `docker compose restart`, waits for health, then runs `pytest -m persistence`.
8. Records `docker compose ps`, scans the logs, evidence files and tracked files for the `.env`
   passwords, and counts 5xx responses in the gateway's JSON access log.

Evidence goes to `./data/fixtures/e2e/`, which is git-ignored: `steps.txt` (one line per step with
its exit status), pytest output and JUnit XML, `gdalinfo-clip.txt`, `perf.json`, the negative
import outputs, `compose-*.txt` and `browser/`.

On Windows, run the scripts in Git Bash. They set `MSYS_NO_PATHCONV=1` so that `/data/...`
arguments are not rewritten into Windows paths.

## Test files

| File | Covers |
|---|---|
| `test_10_catalog.py` | `catalog.datasets`/`current_datasets` metadata (year, CRS, bbox, NoData, units, provenance, paths); `geo.admin_units` geometry, GiST indexes and spatial filters; least-privilege service roles; seed idempotence; synthetic labelling |
| `test_20_ogc_features.py` | Landing page, conformance (no Coverages claim), collections, items, item by id, bbox, paging, CRS84 default and EPSG:4326 lat/lon with `Content-Crs`, public links, OpenAPI |
| `test_30_tiles.py` | OGC API - Tiles conformance, tilesets, tileset metadata and limits, TileMatrixSets, PNG tile pixels (valid opaque, NoData transparent), tile errors; vendor TiTiler listing, TileJSON, point values, unsafe-asset refusal |
| `test_40_processing.py` | Zonal statistics: Town A/B × 2001/2011 = 100/200/25/50; AOI from OGC Features; EPSG:3857 AOI; NoData exclusion (36 valid, 64 excluded); disjoint error. Clip: GeoTIFF/COG round-trip (CRS, transform, bounds, NoData, values); job status; download headers |
| `test_50_negative.py` | Malformed AOIs, CRS errors, unknown/vector datasets, dataset-id injection, formats, out-of-bounds, job ids, body size, extra fields; no mutation route on any prefix; no DB host port; negative ingestion outcomes and an intact catalog |
| `test_60_viewer.py` | Viewer served via the gateway; pinned MapLibre with SRI; discovery-driven code; redirects keep host:port |
| `test_70_persistence.py` | After a restart: catalog, features, tiles, zonal values and the earlier export (same SHA-256) |
| `test_80_performance.py` | Median/p95 latencies on the fixtures, written to `perf.json` (informational) |
| `browser/check_viewer.py` | Headless-Chromium run of the viewer |

## Module suites against the integrated stack

`run_module_suites.sh` starts a separate project, `cgis-modtest`, on gateway port 18088. Its
`PUBLIC_BASE_URL` is `http://gateway:8080` and `OGC_REFRESH_INTERVAL` is 5, which is what
Agent 4's tests expect. It then runs:

- **Agent 2:** pytest against PostgreSQL 17 / PostGIS 3.5. It creates and drops its own
  `cgis_test_*` databases.
- **Agent 3:** pytest with `INGESTION_TEST_SCHEMA=existing`, against a disposable
  `cgis_ingest_test` database migrated with Agent 2's migrations.
- **Agent 4:** the unit and HTTP integration tests through the integrated gateway, and the
  pygeoapi adapter unit tests inside the `ogc` image.
- **Agent 5:** the Docker `test` stage (the standalone suite) and `pytest -m postgis` against the stack.
- **`--cite`:** the OGC TEAM Engine suites `ets-ogcapi-features10` 1.8 and `ets-ogcapi-tiles10` 1.1.
- **`--qgis`:** Agent 4's headless QGIS 3.44 check.

Tear the project down with `docker compose -p cgis-modtest down -v`.
