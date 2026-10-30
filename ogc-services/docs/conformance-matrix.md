# Conformance matrix — what is standard, what is vendor-specific, what is not claimed

Deployment under test: `ogc` (pygeoapi 0.24.0 + `cgis_ogc` adapter) and `tiles` (titiler.core 2.4.0,
restricted), behind the gateway prefixes `/ogc/` and `/tiles/`. Standards status was checked against the
documents published at docs.ogc.org on 2026-10-30.

Evidence key:
- **CITE-F** — OGC TEAM Engine `ets-ogcapi-features10` 1.8: **179 tests, 173 passed, 0 failed, 6 skipped**.
- **CITE-T** — OGC TEAM Engine `ets-ogcapi-tiles10` 1.1: **16 tests, 15 passed, 0 failed, 1 skipped**.
- **IT** — this repository's external-HTTP tests (`tests/integration/`, 94 tests, all passing).

Reproduce: `tests/cite/run_cite.sh` and `docker compose -f tests/stack/compose.yaml run --rm tests`
(full transcripts in `docs/evidence/`).

## 1. Conformance classes declared at `GET /ogc/conformance`

The declaration is **exactly** this list; `tests/integration/test_features.py::test_conformance_declares_exactly_what_is_supported` fails on any difference.

| Standard (document, status) | Conformance class (`…/conf/…`) | Evidence | Notes |
|---|---|---|---|
| OGC API - Common - Part 1: Core 1.0 (19-072, approved) | `ogcapi-common-1/1.0/conf/core`, `landing-page`, `json`, `html`, `oas30` | CITE-F (landing page, conformance, API definition), IT | |
| OGC API - Features - Part 1: Core 1.0 (17-069r4, approved) | `ogcapi-features-1/1.0/conf/core` | CITE-F, IT | collections, items, item, `bbox`, `limit`/paging, `datetime`, errors |
| ″ | `…/conf/geojson` | CITE-F, IT | `application/geo+json` (adapter fix, see §4) |
| ″ | `…/conf/html` | CITE-F, IT | |
| ″ | `…/conf/oas30` | CITE-F, IT (OpenAPI document validated with `openapi-spec-validator`) | |
| OGC API - Features - Part 2: CRS by Reference 1.0 (18-058r1, approved) | `ogcapi-features-2/1.0/conf/crs` | CITE-F (Part 2 tests), IT | `crs`, `bbox-crs`, `Content-Crs`; CRS84 (default), EPSG:4326 (lat/lon order), EPSG:3857 |
| OGC API - Features - Part 3: Filtering 1.0 (19-079r2, approved 2024) | `ogcapi-features-3/1.0/conf/queryables`, `queryables-query-parameters` | IT only (no CITE suite run) | `/queryables`; `?name=Town%20B` |
| CQL2 1.0 (21-065r2, approved 2024) | `cql2/1.0/conf/basic-cql2`, `cql2-text` | IT only — a **subset** (=, <>, >, AND, OR, NOT, IS NULL) | Full Basic-CQL2 coverage (e.g. all literal types, CQL2-JSON) not independently verified |
| OGC API - Tiles - Part 1: Core 1.0 (20-057, approved) | `ogcapi-tiles-1/1.0/conf/core` | CITE-T (core tests incl. error conditions), IT | tile URIs from the tileset `item` template |
| ″ | `…/conf/tileset` | CITE-T (via link following), IT (Req 8 A–G checked member by member) | JSON TileSetMetadata (TMS 2.0 model) |
| ″ | `…/conf/tilesets-list` | CITE-T, IT | |
| ″ | `…/conf/geodata-tilesets` | CITE-T, IT | collection link `rel=…/tilesets-map` → `/collections/{id}/tiles` |
| ″ | `…/conf/png` | IT (PNG 256×256 RGBA, NoData transparent, one portrayal rule per tileset) | CITE-T 1.1 has no PNG-specific tests |
| ″ | `…/conf/oas30` | CITE-T (API definition + operationId suffix tests), IT | tileset path added by the adapter |

CITE-T 1.1 documents that it tests the Core class; its run also exercises tilesets-list and
geodata-tilesets resources. The single skip is `landingPageRetrieval` for `dataset-tilesets`, which is
correctly **not** declared. The CITE-F skips are bbox tests whose fixed test boxes (Europe, Americas,
poles) contain no synthetic features, and the "unknown query parameter" test (pygeoapi accepts free-form
queryable parameters).

## 2. Classes deliberately NOT declared

| Class | Why |
|---|---|
| `ogcapi-tiles-1/1.0/conf/mvt` | pygeoapi declares it for any tile provider; only PNG map tiles are served. |
| `ogcapi-features-4/1.0/conf/create-replace-delete` | Part 4 is a draft (20-002r1); pygeoapi declares it for any feature provider; no resource is editable. |
| `ogcapi-common-2/1.0/conf/collections` | Common Part 2 is a draft (20-024, 1.0.0rc2). `/collections` is provided per Features Part 1, which OGC 20-057 §11.2 accepts for geodata tilesets. |
| `ogcapi-common-3/1.0/conf/schemas`, `…/advanced-property-roles` | draft; the `/schema` endpoint exists but is not verified. |
| `ogcapi-tiles-1/1.0/conf/dataset-tilesets`, `collections-selection`, `datetime`, `jpeg`, `tiff`, `netcdf`, `geojson`, `xml` | not implemented. |
| OGC API - Coverages (19-087, **Candidate SWG Draft 1.0.0rc4**) | not attempted. Raster *data* access stays with the custom processing API (Agent 5). |
| OGC API - Maps, Processes, Records, EDR | not configured. pygeoapi still links `/processes` from the landing page; it returns an empty list and no Processes class is declared. |
| WMS/WFS/WCS | non-goal. |

The adapter refuses to start (`UnsupportedConfiguration`) if the generated configuration ever contains
an editable provider, a non-PNG or non-adapter tile provider, a process, or another provider type —
the situations in which the corrected declaration would become false.

## 3. Route map: standard vs vendor-specific

| Public route | Standard? | Description |
|---|---|---|
| `/ogc/` | OGC API Common landing page | |
| `/ogc/conformance` | OGC API Common | §1 list |
| `/ogc/openapi` | OGC API Common oas30 | OpenAPI 3.0 |
| `/ogc/collections`, `/ogc/collections/{id}` | OGC API Features Part 1 | one collection per ready catalog dataset |
| `/ogc/collections/{vectorId}/items[/{featureId}]` | OGC API Features Part 1/2/3 | GeoJSON, HTML, JSON-LD, CSV |
| `/ogc/collections/{vectorId}/queryables` | OGC API Features Part 3 | |
| `/ogc/collections/{rasterId}/tiles` | OGC API Tiles tilesets-list + geodata-tilesets | |
| `/ogc/collections/{rasterId}/tiles/{tileMatrixSetId}` | OGC API Tiles tileset | `?f=json` (TileSetMetadata), `?f=html`, `?f=tilejson` (TileJSON 3.0, WebMercatorQuad only — an alternative encoding, not an OGC class) |
| `/ogc/collections/{rasterId}/tiles/{tileMatrixSetId}/{tileMatrix}/{tileRow}/{tileCol}` | OGC API Tiles core + png | `?f=png` or `Accept: image/png` |
| `/ogc/TileMatrixSets[/{id}]` | TMS 2.0 definitions, linked with `rel=…/tiling-scheme(s)` | pygeoapi uses `/TileMatrixSets` (capital T); OGC 20-057 only *recommends* `/tileMatrixSets` and only for custom TMSs — both TMSs here are registered (`tileMatrixSetURI`) |
| `/tiles/datasets`, `/tiles/datasets/{id}` | **vendor-specific** (CyberGIS) | discovery of servable rasters |
| `/tiles/datasets/{id}/{tms}/tilejson.json` | **vendor-specific** (TiTiler) | TileJSON 3.0 |
| `/tiles/datasets/{id}/tiles/{tms}/{z}/{x}/{y}[.png]` | **vendor-specific** (TiTiler) | note `{z}/{x}/{y}` order and render parameters (`rescale`, `colormap_name`) |
| `/tiles/datasets/{id}/tiles`, `/tiles/datasets/{id}/tiles/{tms}` | **vendor-specific** | TiTiler's own OGC-Tiles-*style* documents. They are not part of an OGC API (no landing page/conformance/collections) and are not counted as conformance. |
| `/tiles/datasets/{id}/info`, `/statistics`, `/point/{lon},{lat}`, `/info.geojson` | **vendor-specific** (TiTiler) | |
| `/tiles/tileMatrixSets[/{id}]`, `/tiles/healthz`, `/tiles/readyz`, `/tiles/docs` | **vendor-specific** | |

## 4. pygeoapi 0.24.0 behaviours corrected by `cgis_ogc` (and why)

| # | Upstream behaviour | Requirement | Correction | Test |
|---|---|---|---|---|
| 1 | declares `tiles …/conf/mvt` for any tile provider | 20-057 Req 7 | replaced by `…/conf/png` | `test_conformance_declares_exactly_what_is_supported` |
| 2 | declares Features Part 4 CRUD for any feature provider | 17-069r4 §7.4 (declare only what is implemented) | withdrawn | same |
| 3 | declares draft Common Parts 2/3 classes | — (draft) | withdrawn | same |
| 4 | OpenAPI lacks `/collections/{id}/tiles/{tileMatrixSetId}` | 20-057 Req 22 (oas30/completeness) | path added, operationId `….collection.map.getTileSet` | CITE-T `apiDefinitionOperationIdValidation`, IT |
| 5 | OpenAPI lacks `datetime` on items without `time_field` | 17-069r4 Req 25 | inline Req 25 parameter | CITE-F `timeParameterDefinition` (failed before the fix) |
| 6 | `datetime` on such collections → HTTP 500 | 17-069r4 Req 26 C, query-param-invalid | matches all features; malformed → 400 | `test_datetime_matches_features_without_temporal_properties` |
| 7 | items/item sent as `application/json` even for `Accept: application/geo+json` | 17-069r4 /req/geojson/definition | `application/geo+json` | `test_items_feature_collection` |
| 8 | tile without `f` and `Accept: */*` → 400; `?f=jpeg` silently returns PNG | 20-057 §7.1.1 (content negotiation) | PNG by negotiation, 406 for unacceptable `Accept`, 400 for other `f` | `test_tile_selected_by_content_negotiation` |
| 9 | tile error responses labelled `image/png` | HTTP semantics | `application/json` OGC exception | `test_out_of_range_tiles_are_404` |
| 10 | provider errors in tileset metadata → HTTP 500 | 20-057 Req 6 | OGC exception with the provider's status (404/400) | `test_unknown_tileset_and_collection` |
| 11 | raster tile providers use rel `tilesets-raster` (WMTS facade) | 20-057 Req 13 | our provider uses `tile_type = "map"` → `tilesets-map`, `dataType: map` | `test_collection_links_to_map_tilesets` |
| 12 | HTML tiles viewer only renders `tile_type == 'raster'` | — | template derived at build time to accept `map` | `test_html_tiles_viewer_renders_map_tiles` |

The adapter checks `pygeoapi.__version__ == "0.24.0"` and refuses to run on any other version, so an
upgrade forces these corrections to be re-validated.

## 5. Known gaps (not blocking P0, documented rather than hidden)

- Items paging: the last page still carries a `next` link (an empty page follows). 17-069r4 only makes
  `next` a SHOULD when more data exists; not a violation, but clients may make one extra request.
- Item `prev`/`next` links on a dataset scoped by `static_filter` can point at IDs of other datasets
  sharing `geo.admin_units` (pygeoapi computes them on the whole table). Only arises once several vector
  datasets share the table; see `docs/contract-change-requests/agent4.md`.
- Map tile portrayal: by default each raster is scaled over its own range, so 2001 (value 1) and 2011
  (value 2) look identical. The rule is published in each tileset's `style`; set `OGC_TILE_RESCALE=0,2`
  (or a catalog `render.rescale` hint) for a shared scale.
- No OGC API Tiles `datetime`/`collections-selection` or dataset-level tilesets.
- QGIS interoperability: see `docs/usage.md` §4 for what was and was not run.
