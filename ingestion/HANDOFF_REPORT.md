# HANDOFF REPORT — Agent 3 (Ingestion)

| Item | Value |
|---|---|
| Branch | `phase2/agent3-ingestion` (pushed to `origin`; no PR) |
| BASE_SHA | `cc395c69ee738b4597684cf2ccfb84254c15723c` (`origin/phase2/base`) |
| Implementation commit | `068fca51997a7769586986e9ffd17db962714b43` |
| Branch HEAD | The commit that adds this report, a child of the implementation commit. Use `git rev-parse origin/phase2/agent3-ingestion`. |
| Ancestry | `git merge-base --is-ancestor cc395c6 HEAD` → true; no merges, no other branches touched |

## Files changed (all inside owned paths)

- `ingestion/`: the package (`cli.py`, `fixtures.py`, `importer.py`, `raster.py`, `vector.py`,
  `catalog.py`, `manifest.py`, `paths.py`, `errors.py`), `Dockerfile`, `requirements*.txt`, `pytest.ini`,
  `tests/` (5 test modules, plus `tests/sql/contract_schema.sql`, a test-only schema stand-in),
  `scripts/with_temp_postgis.sh`, `examples/`, `docs/`, `README.md` (replaces the baseline placeholder),
  and this report.
- `docs/contract-change-requests/agent3.md`: index of the CCRs, as the baseline README instructs.
- Not touched: `contracts/`, `database/`, root Compose/Makefile/.env, any other agent's module.

## Commands (stable interface for Agents 1, 4, 5, 6)

```bash
python -m ingestion.cli generate-fixtures --out-dir /data/fixtures   # SYNTHETIC fixtures
python -m ingestion.cli validate --manifest /data/fixtures/manifest.json
python -m ingestion.cli import   --manifest /data/fixtures/manifest.json [--assets-dir /data/assets] [--dsn ...]
python -m ingestion.cli seed                                           # generate + import, idempotent
docker build -t cgis-ingestion ingestion/                              # entrypoint = python -m ingestion.cli
```

Env: `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` (or `INGESTION_DATABASE_URL` / `DATABASE_URL`),
`INGESTION_ASSETS_DIR` (default `/data/assets`), `INGESTION_FIXTURES_DIR` (default `/data/fixtures`),
`INGESTION_DB_WAIT_SECONDS`. Exit codes: 0 ok, 1 a dataset failed, 2 bad manifest, 3 DB unavailable or unmigrated.

## Prerequisites

- PostGIS with the `catalog.datasets` / `catalog.current_datasets` / `geo.admin_units` contract
  (Agent 2's migrations) applied **before** `import`/`seed`.
- `/data/assets` writable by the ingestion container only; consumers mount it read-only.
  Wiring is requested in `ingestion/docs/compose-request.md`.

## Outputs and contracts implemented

| Output | Location |
|---|---|
| Fixture rasters | `/data/fixtures/synthetic_2001.tif`, `synthetic_2011.tif` (64×64 float32, EPSG:4326, NoData −9999 on rows/cols 0–3, values 1/2) |
| Fixture vectors | `/data/fixtures/synthetic_towns.geojson` (town-a, town-b), `synthetic_test_aois.geojson` (aoi-nodata-overlap, aoi-disjoint; not imported) |
| Expected answers | `/data/fixtures/expected_values.json` (pixel-center, `all_touched=false`) |
| Manifest | `/data/fixtures/manifest.json` (3 datasets; relative file paths) |
| COG assets | `/data/assets/rasters/<id>/v<n>/<id>_v<n>.tif` (DEFLATE+predictor, 512 tiles, NEAREST overviews) |
| Vector assets | `/data/assets/vectors/<id>/v<n>/<id>_v<n>.geojson` (EPSG:4326 MultiPolygon) |
| Catalog rows | `catalog.datasets` (status `ready`), visible in `catalog.current_datasets`; `asset_path` relative to `/data/assets` |
| Units | `geo.admin_units` (`town-a` “Town A”, `town-b` “Town B”, level `synthetic_town`, MultiPolygon 4326) |

Fixture IDs follow the contract: `synthetic-worldpop-2001`, `synthetic-worldpop-2011`, `synthetic-towns`.
Known answers: Town A 100 px, sums 100 / 200. Town B 25 px, sums 25 / 50. NoData-overlap AOI 36 valid
of 100, sums 36 / 72. The disjoint AOI must produce a no-overlap error.

Provenance written per dataset: the manifest provenance plus `ingestion` (importer version, GDAL
version, source/asset sha256, timestamps, COG details, or `error` on failure). Rasters also get a
`raster` block (grid, CRS, native bounds, NoData, dtype, valid/nodata counts, sum/min/max); vectors get
a `vector` block (unit_ids, source CRS, property mapping).

## Commands run and results (2026-10-30, Fedora 42, Python 3.12, rasterio 1.4.3 / GDAL 3.9.3, PostgreSQL 16 + PostGIS 3)

| Command | Result |
|---|---|
| `.venv/bin/python -m pytest ingestion/tests` (no DB) | **65 passed, 19 skipped** (the DB tests skip without `INGESTION_TEST_DSN`) |
| `ingestion/scripts/with_temp_postgis.sh python -m pytest ingestion/tests` | **84 passed** |
| Same, from a clean `git archive HEAD` export | **84 passed** |
| `podman build ingestion/` | success (`python:3.12.7-slim-bookworm` + `libexpat1`) |
| Container `seed` against a temp PostGIS | exit 0; 3 × `imported` |
| Container `import` again | exit 0; 3 × `unchanged` (idempotent) |
| Container `python -m pytest ingestion/tests` against a temp PostGIS | **84 passed** |
| `gdalinfo` on the produced COG | `LAYOUT=COG`, `COMPRESSION=DEFLATE`, `PREDICTOR=3`, NoData −9999, unit `people_per_cell`, `SYNTHETIC=TRUE`, origin (78, 18), pixel 0.001 |
| `sha256sum -c` of `contracts/` | all 7 OK (unchanged) |

Fixture checksums were identical on host and in the container (same GDAL wheel):
`synthetic_2001.tif` `92b0df56…`, `synthetic_2011.tif` `6940c9ad…`, `synthetic_towns.geojson` `5af30731…`.

Test coverage against `TEST_PLAN.md`:
- Determinism: JSON outputs are byte-identical; rasters have the same profile, tags and pixels.
- Grid, transform, bounds and NoData layout.
- Town and AOI sums, computed by rasterizing with `all_touched=false`.
- COG validity, tiling and overviews; values, CRS, NoData and units preserved.
- Ready catalog rows have correct year, units, NoData, bbox, resolution and provenance.
- `current_datasets` picks the highest version, and older versions are kept.
- Idempotent re-import leaves rows, including `updated_at`, and asset mtimes unchanged.
- A conflicting checksum is rejected, and the asset and row are left untouched.
- A simulated mid-import failure (COG verification, or a crash inside the publish transaction) leaves
  no ready row, no final asset and no units; the row becomes `failed` and a retry succeeds.
- An existing different asset is never overwritten; an identical leftover is adopted.
- The no-hard-link filesystem fallback works.
- Concurrent imports are blocked by the advisory lock.
- Corrupt and truncated TIFFs, missing CRS or transform, rotated grids, missing NoData, undeclared NaN,
  all-NoData rasters, missing bands and `expect` mismatches are rejected.
- Bowtie, point, line, empty and duplicate-id features, missing id or name, out-of-range coordinates,
  unknown CRS, GPKG, missing files, unsupported kinds and bad slugs are rejected.
- A projected UTM raster (100 m, uint16, band 2 of 3) and a reprojected legacy-`crs` GeoJSON import
  without synthetic assumptions.
- The schema check rejects an unmigrated DB, and CLI exit codes are tested.

## Deviations, requests and integration notes

1. **The DB schema is not Agent 2's yet.** Tests use `ingestion/tests/sql/contract_schema.sql`, written
   from the frozen contract with deliberately strict constraints. **Agent 6:** rerun with
   `INGESTION_TEST_SCHEMA=existing INGESTION_TEST_DSN=<disposable *test* db with Agent 2 migrations>`.
2. **CCR-3.1 (optional, additive):** add `dataset_id`, `dataset_version`, `properties`, `source` to
   `geo.admin_units`. The importer populates them when present. Until then, Agent 4 can map
   collection → units through `catalog.current_datasets.provenance->'vector'->'unit_ids'`.
3. **CCR-3.2:** `catalog.datasets.units` must allow NULL for vectors.
   **CCR-3.3:** failure details are stored in `provenance.ingestion.error`.
   **CCR-3.4:** `geo.admin_units` holds the latest imported version of each unit.
   See `ingestion/docs/contract-change-request.md`.
4. **Compose wiring** (Agent 1/6): `ingestion/docs/compose-request.md`. It covers the service, the
   read-write `assets` volume (read-only for tiles/processing), env vars, `depends_on` migrations, and
   `seed` for the Makefile.
   - **SELinux/podman hosts need `:z` on bind mounts.** Without it, fixture writes fail with
     `fixture_write_failed`.
   - The container runs as uid 10001.
5. The manifest accepts the contract's single-object shape, plus a list or `{"datasets": [...]}`.
   The generated manifest uses the `{"datasets": [...]}` form with paths relative to the manifest.
   Absolute `/data/fixtures/...` paths (as in the contract example) also work: see `examples/`.
6. The vector catalog row uses `crs = EPSG:4326` (the CRS of the stored asset and `geo.admin_units`);
   the source CRS is recorded in `provenance.vector.source_crs`.
7. For consumers (Agents 4/5):
   - Always resolve datasets via `catalog.current_datasets` and the relative `asset_path` under `/data/assets`.
   - Ignore `/data/assets/.staging/`.
   - Never accept client paths.
   - Nothing in the importer hard-codes the synthetic IDs; only tests and the generator reference them.

## Known limitations

- Vector input is GeoJSON only; GPKG/Shapefile are rejected with `unsupported_format`.
- Invalid geometries are rejected, not repaired; that is by design.
- Rotated, GCP-only or non-EPSG rasters are rejected. Multi-band sources publish one selected band per dataset.
- Validation decodes every pixel, so very large rasters take time proportional to their size.
- If validation fails before CRS/bbox are known (e.g. an unreadable file), no `failed` row can be
  inserted under a strict NOT NULL schema. The error is reported by the CLI only.
- A crashed run that leaves a *different* file at a version's final asset path blocks that version
  (`asset_exists`) until an operator removes the file or publishes a new version. This is deliberate:
  the importer never overwrites.
- Antimeridian-crossing rasters would get a world-wide bbox from the densified transform. This is not
  expected for the target region.

## Security and provenance

- No secrets, tokens or local paths are in the repo. DSN passwords are redacted from errors.
- The container runs as non-root.
- Every fixture file, manifest entry, catalog title and provenance record, and every COG tag, says
  `SYNTHETIC` / `deterministic-fixture`. The data is **not real population data, not WorldPop, not a
  scientific estimate**.
- No WorldPop data was downloaded. No interpolation, redistribution, LULC or modelling code is included.
