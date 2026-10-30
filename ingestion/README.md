# `ingestion/` — validation, COG conversion, catalog registration, SYNTHETIC fixtures

**Owner:** Agent 3. Implements the ingestion contract in `contracts/SHARED_CONTRACTS.md` and the fixture
spec in `contracts/SYNTHETIC_DATA.md`. No interpolation, redistribution or population modelling happens here.
The importer is metadata-driven: nothing in it depends on the synthetic IDs, years, grid, CRS or region.

## Commands

From the repository root (local venv) or as the container entrypoint (`python -m ingestion.cli`):

| Command | What it does |
|---|---|
| `python -m ingestion.cli generate-fixtures --out-dir /data/fixtures` | Writes the deterministic SYNTHETIC rasters, towns, test AOIs, `expected_values.json` and `manifest.json` |
| `python -m ingestion.cli validate --manifest PATH` | Reads and validates every dataset; writes nothing and needs no database |
| `python -m ingestion.cli import --manifest PATH` | Validates, builds the COG or normalized GeoJSON asset, and registers it in `catalog.datasets` / `geo.admin_units` |
| `python -m ingestion.cli seed` | `generate-fixtures` into `$INGESTION_FIXTURES_DIR` (default `/data/fixtures`), then `import` of its manifest |

Common options: `--assets-dir` (default `$INGESTION_ASSETS_DIR` or `/data/assets`), `--dsn`,
`--wait-db SECONDS`, `--only DATASET_ID` (repeatable), `--fail-fast`.
Database connection order: `--dsn`, then `$INGESTION_DATABASE_URL`, then `$DATABASE_URL`, then the libpq
`PG*` variables (`PGHOST=db`, `PGPORT=5432`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`). Passwords are never logged.

stdout gets one JSON object per dataset, with `outcome` set to `imported`, `unchanged`, `validated` or
`failed`. A failed dataset also carries `error.code`/`error.message`. Logs go to stderr.
Exit codes: `0` ok, `1` at least one dataset failed, `2` manifest unreadable, `3` database unavailable.

Compose usage (wiring requested in [`docs/compose-request.md`](docs/compose-request.md)):

```bash
docker compose run --rm ingestion seed                       # fixtures + import, idempotent
docker compose run --rm ingestion import --manifest /data/fixtures/manifest.json
```

## Manifest

Accepts the contract's single-object shape, a JSON list, or `{"datasets": [...]}`. Relative `file`
paths resolve against the manifest's directory. Examples are in [`examples/`](examples/).

| Field | Required | Notes |
|---|---|---|
| `dataset_id` | yes | lowercase slug `^[a-z0-9][a-z0-9._-]*[a-z0-9]$`, max 128 chars |
| `version` | yes | positive integer; `(dataset_id, version)` is immutable once `ready` |
| `title`, `source`, `kind` (`raster`/`vector`), `product_level` (`source`/`level1`/`level2`/`other`), `file` | yes | synthetic datasets must say `SYNTHETIC` in the title |
| `units` | raster: yes | e.g. `people_per_cell`; stored verbatim, never converted |
| `year`, `description`, `provenance` (object) | no | `provenance` is merged into the catalog's provenance JSON |
| `band` | no | source band to publish (default 1); the COG holds that single band |
| `checksum_sha256` | no | if given, the file must match it |
| `expect` | no | assertions checked against the file: `crs`, `width`, `height`, `band_count`, `dtype`, `nodata`, `resolution` `[x,y]`, `bounds` `[w,s,e,n]` (native CRS), `tolerance`, `feature_count` |
| `allow_missing_nodata` | no | rasters must declare NoData (or an internal mask) unless this is `true` |
| `id_property`, `name_property`, `level_property`, `unit_id_prefix` | no (vector) | defaults `unit_id`, `name`, `level`, `""` |

CRS, transform, bounds, resolution, dtype and NoData are always **read from the file**, never trusted
from the manifest. Unknown keys produce a warning and are kept in `provenance.manifest_extra`.

## What import guarantees

* **Raster validation:** the file must be readable end to end (every block is decoded) and have the
  required band. It needs an EPSG-identifiable CRS and a north-up affine geotransform with no rotation.
  It must declare NoData or a mask, and the NoData value must fit the dtype. Valid cells may not contain
  undeclared NaN/Inf, and at least one cell must be valid. The WGS84 bbox is computed with a densified
  transform.
* **COG:** GDAL COG driver with DEFLATE + predictor (lossless), 512 px tiles, AUTO overviews with
  NEAREST resampling (display only). The COG is then checked with `rio_cogeo.cog_validate` and
  compared with the source, block by block: values, mask, CRS, transform, dtype, NoData and units must
  match. Band units and the `CGIS_DATASET_ID`/`CGIS_DATASET_VERSION` tags (and `SYNTHETIC=TRUE` for
  fixtures) are embedded. A source that is already a COG is still rewritten into the managed layout,
  and `provenance.raster.source_is_cog` records that it was one.
* **Vector:** GeoJSON FeatureCollection of Polygon/MultiPolygon. RFC 7946 WGS84 is the default; a
  legacy `crs` member (e.g. `urn:ogc:def:crs:EPSG::32644`) is reprojected to EPSG:4326 with lon/lat
  axis order. Z values are dropped. Invalid, empty, zero-area, non-polygon, out-of-range or duplicate-id
  features are **rejected, never silently repaired**. Output is MultiPolygon with RFC 7946 winding. Rows
  are upserted into `geo.admin_units` and a normalized GeoJSON asset is stored. GPKG is not supported in v1.
* **Asset paths:** `rasters/<id>/v<version>/<id>_v<version>.tif` and
  `vectors/<id>/v<version>/<id>_v<version>.geojson`, relative to `/data/assets`, built only from the
  validated slug. Path joins reject absolute paths, `..` and symlink escapes. Work happens in
  `/data/assets/.staging/` (consumers should ignore that directory).
* **Atomicity / idempotence:** each `(dataset_id, version)` import holds a PostgreSQL advisory lock.
  1. Ready with the same checksum: no-op (`unchanged`). Nothing is rewritten and the asset's recorded
     checksum is re-verified.
  2. Ready with a different checksum: `checksum_conflict`. Nothing is touched.
  3. Otherwise the row is written as `staged`, and the asset is built and verified in staging.
  4. The asset is placed with a no-clobber hard link. An existing *different* file is never overwritten
     (`asset_exists`); an identical one left by an interrupted run is adopted.
  5. Vector units are upserted and the row is set to `ready` in **one transaction**.

  Any failure removes this attempt's files and marks the row `failed`, with the error in
  `provenance.ingestion.error`. A later retry reuses the row. A higher ready version becomes current in
  `catalog.current_datasets`; older versions and their assets are kept.

## SYNTHETIC fixtures (NOT real population data)

`generate-fixtures` produces exactly the contents of `contracts/SYNTHETIC_DATA.md`. The rasters are
64×64 float32, EPSG:4326, with the upper-left corner at (78.000, 18.000) and 0.001° pixels. NoData is
−9999 on rows 0–3 and columns 0–3, and valid cells are 1 (2001) or 2 (2011). `expected_values.json`
gives the pixel-center (`all_touched=false`) answers:

| AOI | valid px | 2001 sum | 2011 sum |
|---|---:|---:|---:|
| `town-a` | 100 | 100 | 200 |
| `town-b` | 25 | 25 | 50 |
| `aoi-nodata-overlap` (78.000–78.010, 17.990–18.000; 100 cells) | 36 (64 NoData) | 36 | 72 |
| `aoi-disjoint` (79.000–79.010, 19.000–19.010) | — | no-overlap error | — |

The test AOIs live in `synthetic_test_aois.geojson` and are not imported. Output is deterministic:
the JSON files are byte-identical across runs, and the TIFFs have identical profile, tags and pixels
(and are byte-identical on the same GDAL build). Fixtures are generated, not committed; `/data/` is git-ignored.

## Tests

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r ingestion/requirements-dev.txt
.venv/bin/python -m pytest ingestion/tests                       # unit tests; DB tests skip
PATH=$PWD/.venv/bin:$PATH ingestion/scripts/with_temp_postgis.sh python -m pytest ingestion/tests   # + PostGIS
```

DB tests use `INGESTION_TEST_DSN`, and the database name must contain `test` because the tests are
destructive. By default they load `tests/sql/contract_schema.sql`, a test-only stand-in written from
the frozen contract. During integration, set `INGESTION_TEST_SCHEMA=existing` to run against Agent 2's
real migrations instead.

## Related docs

* [`docs/PHASE1_HANDOFF.md`](docs/PHASE1_HANDOFF.md): what real Phase 1 deliveries must contain.
* [`docs/contract-change-request.md`](docs/contract-change-request.md): proposed additive DB columns.
* [`docs/compose-request.md`](docs/compose-request.md): service, volume and env wiring for Agent 1/6.
* [`HANDOFF_REPORT.md`](HANDOFF_REPORT.md): branch, SHA, results, limitations.
