# Agent 2 (PostGIS): handoff report

## Branch and commits

| | |
|---|---|
| Branch | `phase2/agent2-postgis` (pushed to `origin`; no PR, no merges) |
| Based on | `BASE_SHA` = `cc395c69ee738b4597684cf2ccfb84254c15723c` (`origin/phase2/base`) |
| Implementation commit | `e4135ee`. The HEAD that adds this report is given in the operator summary and by `git rev-parse origin/phase2/agent2-postgis` |
| Ancestry | linear: `cc395c6 → e4135ee → (this report)` |

## Files changed

Everything is under Agent 2's own paths, apart from one pointer file in the shared requests folder:

- `database/**`: migrations, the `cgis_db` package, tests, docs, the stub fixture, this report and
  the README (which replaces Agent 1's placeholder).
- `docs/contract-change-requests/agent2.md`: a pointer that states **no v1 contract change is
  required**.

Untouched: `contracts/`, root Compose, `.env*`, `Makefile`, and the other agents' modules.

## Delivered

- **Migrations** (`database/migrations/core`):
  - `0001` adds PostGIS and version guards (PostgreSQL ≥ 14, PostGIS ≥ 3.1).
  - `0002` adds the `cgis` helpers: safe relative paths, `EPSG:` → SRID, `to_4326`, densified bbox
    reprojection.
  - `0003` adds `catalog.datasets`, `catalog.current_datasets` and `register_dataset`/`set_dataset_status`.
  - `0004` adds `geo.admin_units` and the spatial primitives.
  - `0005` adds `ops.jobs` and the lifecycle helpers.
  - `0006` adds the NOLOGIN roles `cgis_reader`, `cgis_ingest` and `cgis_processing`.
  - Every migration has a `.down.sql`. None uses `CASCADE`.
- **Optional track** (`migrations/raster_demo`): PostGIS Raster tiles with `ST_Clip` and
  `ST_SummaryStatsAgg`. It is separate from the COG storage and documented in `docs/RASTER_DEMO.md`.
- **Migration CLI:** `python -m cgis_db migrate up|down|status`. It uses per-migration transactions,
  an advisory lock, a SHA-256 drift check (CRLF-safe), and `--target`/`--steps`.
- **Fixture loader:** `python -m cgis_db fixtures load [--geojson PATH]`. It is independent of
  Agent 3's importer, idempotent, and rejects conflicts.
- **Query library:** `cgis_db/queries.py`, which binds every value as a parameter.
- **Docs:** `README.md` (ERD, schema, index rationale, rollback constraints, environment variables,
  examples), `docs/EXPLAIN_ANALYZE.md`, `docs/RASTER_DEMO.md` and
  `docs/contract-change-request.md`.

## Database environment variables

| Variable | Use |
|---|---|
| `CGIS_DATABASE_URL`, `DATABASE_URL` | connection URL, in that order |
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`/`PGPASSFILE`, `PGSSLMODE` | libpq fallback |
| `CGIS_DB_MIGRATIONS_DIR` | optional migrations directory override |
| `CGIS_TEST_ADMIN_DSN` | tests only; needs `CREATEDB` |

No credentials are stored in the repository.

## Migration command (for Agent 1/6 Compose wiring)

```bash
python -m cgis_db migrate up            # after db is healthy, before ingestion/ogc/processing
python -m cgis_db fixtures load --geojson /data/fixtures/synthetic_towns.geojson   # optional seed
```

The command is safe to run repeatedly and from several containers at once. Exit code 0 means the
schema is current.

## Test environment and results

Docker was **not available** on the development machine. The tests therefore ran against a real
local PostgreSQL 18.4 and PostGIS 3.6.2 server: a throw-away cluster created with `initdb` on port
55432, using trust authentication on localhost. Nothing was mocked.

```bash
cd database
CGIS_TEST_ADMIN_DSN=postgresql://postgres@localhost:55432/postgres python -m pytest
# ======================= 136 passed in 159.44s (0:02:39) =======================
```

| File | Tests |
|---|---:|
| `test_catalog.py` | 54 |
| `test_geo.py` | 32 |
| `test_jobs.py` | 23 |
| `test_migrations.py` | 12 |
| `test_raster_demo.py` | 12 |
| `test_explain_and_cli.py` | 3 |

After the run, no `cgis_test_*` databases were left on the server.

Key evidence:

- **Migrations:** a fresh `migrate up` applies 6 migrations. A second run is a no-op. Three runners
  started concurrently apply the migrations exactly once. Full `down` to 0 followed by `up` works.
  An applied migration that is later edited is refused as drift.
- **Towns:** the synthetic towns load as valid `MULTIPOLYGON` geometries in SRID 4326 with exact
  extents:
  - Town A: 78.010–78.020, 17.980–17.990.
  - Town B: 78.030–78.035, 17.950–17.955.
- **Spatial selection:** bbox and `ST_Intersects` select one town, both or neither, and work with
  AOIs in EPSG:3857 and EPSG:32644.
- **Raster demo** (pixel-centre inclusion):

  | AOI | 2001 sum | 2011 sum | Valid pixels | NoData excluded |
  |---|---:|---:|---:|---:|
  | Town A | 100 | 200 | 100 | 0 |
  | Town B | 25 | 50 | 25 | 0 |
  | NoData-edge AOI | — | — | 16 | 48 |
  | Disjoint AOI | NULL | NULL | 0 | 0 |

  The disjoint AOI returns count 0 and a NULL sum.
- **EXPLAIN:** GiST is used for bbox, projected-AOI and current-view queries on the 22.5k-unit
  benchmark. Transforming the indexed column falls back to a Seq Scan. No speedup is claimed for
  the 2-row fixture.

## Contract

The frozen v1 contract is implemented exactly as written. The additive columns and the two
optional v2 proposals are described in `docs/contract-change-request.md`:

- version-aware `geo.admin_units` identity;
- `catalog.datasets.status_message`.

## Known limitations and caveats

- **Unit identity:** `geo.admin_units.unit_id` is global, as the contract specifies. A new version
  of a boundary dataset replaces the old units in place (see proposal B1). A `unit_id` that belongs
  to a different dataset is rejected.
- **Pixel-centre edge cases:** a pixel centre lying exactly on an AOI edge is ambiguous in both
  PostGIS and GDAL. The tests use AOIs aligned to pixel edges, as `SYNTHETIC_DATA.md` advises.
- **Tile seams:** with PostGIS Raster, `touched=true` clipping applied tile by tile **undercounts at
  tile seams** (81 pixels instead of 100). Analytics should use pixel-centre mode, and the processing
  service should use GDAL/rasterio.
- **Projected bboxes:** a bbox given in a projected CRS is densified (64 segments per side), then
  reprojected and enveloped. This is conservative but approximate for very large or polar extents.
- **Rollback deletes data:** rolling back `0003` or `0004` deletes catalog and unit data. Use `down`
  only for development; in production, roll forward with a new migration.
- **Roles:** `0006` needs `CREATEROLE` to create its roles. Without it, creation is skipped with a
  NOTICE and the grants apply only to roles that already exist.
- **PostGIS version:** PostGIS ≥ 3.6 auto-promotes a Polygon to MultiPolygon at the column typmod.
  Older versions reject it. Either way, `geo.upsert_admin_unit` normalises the geometry.
- **Test fixture:** `fixtures/synthetic_towns.stub.geojson` is a clearly labelled **test stub** that
  follows the contract. Agent 3's generator remains the authoritative fixture source.
