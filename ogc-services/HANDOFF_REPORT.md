# Agent 4 handoff report — OGC services

| | |
|---|---|
| Branch | `phase2/agent4-ogc` (pushed to `origin`; no PR, nothing merged) |
| Based on | `BASE_SHA` `cc395c69ee738b4597684cf2ccfb84254c15723c` (`origin/phase2/base`), no other merges |
| Tested implementation commit | `6e64e00dc142528d86a38b860e39032d12af2dde` |
| Branch HEAD | the commit that adds this report (direct child of `6e64e00`; documentation only). Verify with `git rev-parse origin/phase2/agent4-ogc`. |
| Paths changed | `ogc-services/**` (owned) and `docs/contract-change-requests/agent4.md` (the designated request location). No change to `contracts/`, root files, or other agents' modules. |

## 1. What was delivered

| Task (TASK_SPEC) | Delivered | Where |
|---|---|---|
| 1. pygeoapi on PostGIS: landing, conformance, collections, items, item, bbox, paging, OpenAPI, errors | yes | `pygeoapi/`, `cgis_ogc/pygeoapi_plugins/postgresql.py` |
| 2. `synthetic-towns` with Town A/B, GeoJSON, CRS semantics, external-HTTP tests | yes — CRS84 default, EPSG:4326 lat/lon via `crs`, `bbox-crs`, `Content-Crs` | `tests/integration/test_features.py` |
| 3. Catalog-driven publication, no hand edits | yes — `python -m cgis_ogc.publish` (startup, `refresh`, or every `OGC_REFRESH_INTERVAL` s) | `cgis_ogc/publish.py`, `tests/integration/test_publication.py` |
| 4. TiTiler under `/tiles/`, ready assets only, validated id→path mapping, no SSRF | yes — no `url=` parameter, no `/cog`; catalog lookup, root confinement after symlink resolution, GeoTIFF driver check | `tiler/cgis_tiler/`, `tests/unit/test_assets.py`, `tests/integration/test_tiles_vendor.py` |
| 5. Visual tile tests for 2001/2011; TileJSON | yes — pixel-level checks (valid opaque, NoData transparent), `/point` values 1/2/NoData; TileJSON (vendor, and OGC `?f=tilejson` alternative) | `tests/integration/test_tiles_*.py` |
| 6. Real OGC API Tiles interface, tilesets/tileset/TMS metadata, verified conformance; vendor routes labelled | yes — no P0 gap. Coverages not attempted (draft). | `cgis_ogc/pygeoapi_plugins/titiler_tiles.py`, `cgis_ogc/conformance.py`, `docs/conformance-matrix.md` |
| 7. curl, QGIS directions, route mapping, conformance matrix | yes | `docs/usage.md`, `docs/conformance-matrix.md`, `docs/gateway-request.md` |
| 8. Gateway/env/volume request | yes | `docs/gateway-request.md` |

### Handoff checklist

- [x] Features collection/item/bbox endpoints implemented and tested.
- [x] Raster tile service works for registered COG assets.
- [x] Catalog publication/refresh is reproducible and documented.
- [x] OGC API Tiles endpoints and discovery/TileMatrixSet metadata tested; conformance matrix separates standard, vendor-specific and draft APIs.
- [x] Tile access is constrained to trusted assets.
- [x] Gateway/env integration requests written; Dockerfiles/config in the owned module.
- [x] This report (URLs, commands/results, limitations, SHAs).
- [x] Feature branch pushed only; no PR, no merge to the default branch.

## 2. Public URLs (through the gateway, default `http://localhost:8080`)

| URL | Interface |
|---|---|
| `/ogc/`, `/ogc/conformance`, `/ogc/openapi` | OGC API Common |
| `/ogc/collections`, `/ogc/collections/{id}` | one collection per `ready` catalog dataset |
| `/ogc/collections/synthetic-towns/items[/{featureId}]`, `/queryables` | OGC API Features 1/2/3 |
| `/ogc/collections/synthetic-worldpop-{2001,2011}/tiles[/{tms}[/{tileMatrix}/{tileRow}/{tileCol}]]` | OGC API Tiles 1.0 (PNG map tiles) |
| `/ogc/TileMatrixSets[/{id}]` | TMS 2.0 definitions |
| `/tiles/datasets[/{id}[/{tms}/tilejson.json \| /tiles/{tms}/{z}/{x}/{y}.png \| /info \| /point/{lon},{lat}]]` | **vendor-specific** TiTiler |

## 3. Commands run and results

All on Linux 6.18, Docker 29.9.2, Compose 5.6.1. Transcript: `docs/evidence/test-all.txt`
(clean start: stack and volumes removed first).

| Command | Result |
|---|---|
| `ogc-services/scripts/test-all.sh` | exit 0 |
| └ `pytest tests/unit tests/integration` (in `cgis-ogc-tests`, over HTTP via the gateway stub) | **215 passed** (121 unit, 94 integration) |
| └ `pytest tests/unit_pygeoapi` (inside the `ogc` image, real pygeoapi 0.24.0) | **47 passed** |
| └ OGC CITE `ets-ogcapi-features10` 1.8 (TEAM Engine 5.7) | **179 tests: 173 passed, 0 failed, 6 skipped** |
| └ OGC CITE `ets-ogcapi-tiles10` 1.1 (TEAM Engine 5.7) | **16 tests: 15 passed, 0 failed, 1 skipped** |
| └ headless QGIS 3.44.15 (`tests/qgis/check_qgis.py`) | **10/10 checks passed** |

Raw CITE results: `docs/evidence/cite-ogcapi-*.xml.gz`. Skips are explained in
`docs/conformance-matrix.md` §1 (fixed ETS bboxes outside the fixture area; `dataset-tilesets` not
claimed).

Defects found by these runs and fixed before the tested commit (so they are not re-discovered at
integration): `datetime` on collections without temporal data returned HTTP 500 and was missing from
OpenAPI (CITE `timeParameterDefinition`); GeoJSON served as `application/json`; tile requests with
`Accept: */*` rejected; tile errors labelled `image/png`; a refused asset surfacing as 502 instead of
404; an intermittent CITE NullPointerException caused by pygeoapi's `$ref` to a GitHub `master` file
(now inlined).

## 4. Contracts implemented and deviations

- Reads `catalog.current_datasets` exactly as frozen (no writes). Vectors from `geo.admin_units`.
- `asset_path` treated as relative to `/data/assets`; an absolute path is tolerated only if it resolves
  inside `/data/assets` (lenient, still confined).
- Error bodies on `/tiles/` follow the contract shape `{"error": {"code", "message"}}`. `/ogc/` keeps the
  OGC API exception shape (`code`, `description`), as the standards define it.
- **No contract change was made.** Proposed (non-blocking): `geo.admin_units.dataset_id` and a
  read-only DB role — `docs/contract-change-requests/agent4.md`. Until then, an optional catalog
  provenance hint `ogc_publication.filter` scopes a vector dataset to rows.
- No synthetic dataset id is hard-coded in service code; tests reference them.

## 5. Known limitations and caveats

- **Tested on a stub schema.** The test stack's catalog (`tests/stack/stub_catalog.sql`) and rasters
  (`make_stub_rasters.py`) are test-only stubs following the contract; Agent 2's migrations and Agent 3's
  generator/importer were not available. Integration must re-run the suites on the real stack (see
  README "Testing against the integrated deployment").
- **pygeoapi pinned to 0.24.0.** The adapter patches pygeoapi internals and refuses other versions; an
  upgrade requires re-running the adapter tests and CITE.
- **CITE coverage.** The Tiles ETS 1.1 tests the Core class plus tileset/tilesets-list/geodata
  resources; the PNG class and Features Part 3/CQL2 are covered only by this repo's tests (CQL2: a subset
  of Basic CQL2 operators).
- **The OpenAPI document still `$ref`s schemas.opengis.net** (OGC's canonical schemas) for response
  definitions; offline clients that resolve every reference need internet access. Items parameters
  resolve locally.
- **Portrayal.** Default colour scaling is per dataset, so 2001 and 2011 look identical in OGC map tiles;
  the rule is published in each tileset's `style`. Set `OGC_TILE_RESCALE=0,2` for comparison views.
- Last items page still has a `next` link; item `prev`/`next` may cross datasets sharing a table
  (`docs/conformance-matrix.md` §5).
- pygeoapi links `/processes` from the landing page (empty list, no Processes class declared).
- QGIS: verified headless (QGIS 3.44.15 in Docker); the desktop GUI was not exercised because the local
  QGIS 4.2.3 install fails to start.
- The test stack uses a throwaway local DB password (`cgis-test-only`, overridable via
  `CGIS_TEST_DB_PASSWORD`); production credentials come only from the environment.
- Security scope: read-only services, no mutation endpoints; not a full RBAC (Phase 4).

## 6. Integration notes for Agents 1 and 6

1. Add `ogc` and `tiles` services, the read-only asset mount and gateway routes per
   `docs/gateway-request.md` (prefixes stripped; forward `Host`; `TILES_ROOT_PATH=/tiles`;
   `OGC_PUBLIC_URL` = public `/ogc` URL).
2. Start order: `db` (migrated) → `tiles` → `ogc`. Set `OGC_REFRESH_INTERVAL` (e.g. 30) so datasets imported
   after start-up appear without a restart, or run `docker compose exec ogc /cgis/entrypoint.sh refresh`
   after seeding.
3. Smoke test: section 6 of `docs/gateway-request.md`; full evidence: `scripts/test-all.sh`-equivalent
   commands against the integrated network (README).
4. Expected integrated results with the real fixtures: `synthetic-towns` items → Town A and Town B;
   bbox `78.009,17.979,78.021,17.991` → Town A only; `/tiles/datasets/synthetic-worldpop-2011/point/78.015,17.985`
   → `[2.0]`; CITE Features/Tiles 0 failures.
