# Acceptance criteria and evidence requirements

## P0: Phase 2 is not complete unless all of these pass

1. Fresh clone can build and run via documented Docker Compose commands on a supported local environment.
2. PostGIS extensions, schema migrations, indexes, and persistent volumes initialize deterministically.
3. Synthetic fixture generator creates the two rasters, the town polygons, and manifests with explicitly synthetic provenance.
4. Ingest twice is idempotent; bad CRS/file, conflict on ID/version/checksum, and invalid geometry fail clearly without corrupting ready datasets.
5. `catalog.datasets` exposes correct year, CRS, bbox, NoData, units, provenance and stored paths; `geo.admin_units` supports spatial filtering.
6. OGC API Features landing/conformance/collections/items routes serve Town A/Town B, bbox filtering works, and response CRS follows the declared standard.
7. Tile service renders the synthetic raster without granting users arbitrary server filesystem or remote asset access. An OGC API Tiles publication endpoint exists and its advertised conformance/discovery/TileMatrixSet metadata and tile requests are verified. Vendor TiTiler endpoints are separately identified.
8. Spatial processing returns Town A / 2001 sum=100, 2011 sum=200; Town B / 2001 sum=25, 2011 sum=50 under documented pixel-center convention.
9. Clipping and export produce correctly georeferenced GeoTIFF (CRS, bounds, NoData, values) and a downloadable result; non-overlap/invalid AOI fail explicitly.
10. Health checks, logs, API documentation, safe error responses, and reproducible test commands are available.
11. `phase2/integration` includes all five branches, end-to-end tests pass from a fresh checkout, and no secrets or synthetic data presented as real occur in Git history.

## Recommended test taxonomy

- Unit: schema constraints, pixel arithmetic, geometry/CRS validation, manifest parsing, path safety, error response models.
- Contract: DB catalog column compatibility, payload shapes, OpenAPI validation, fixture IDs/values, mounted file paths.
- Service integration: importer → catalog; catalog → OGC Features; asset → tiles; catalog+asset → spatial processing.
- End-to-end: one actual Docker Compose deployment and curl/QGIS/manual viewer demonstration.
- Negative: unknown dataset, unsupported export format, polygon outside raster, invalid/empty geometry, corrupted TIFF, missing file, duplicate/conflicting metadata, unauthorized write path.
- Operational: full restart, no manual SQL/config edits, idempotent seed, clean logs with no tokens.

## Evidence expected from Agent 6

- Final commit SHA(s), command transcript with exit codes and test counts, `docker compose ps` health, sample OGC response snippets, sample tile request, zonal stats JSON, valid GeoTIFF metadata (`gdalinfo`), database query plan notes, limitations.
- Comparative discussion: PostGIS vector/metadata + COG raster design; optional PostGIS Raster benchmark; verified OGC Features/Tiles vs nonstandard TiTiler paths; Coverages marked draft if used.
- `docs/integration/FINAL_REPORT.md` with reproduction and Phase 3/4 handoff contract.

## Excluded items (do not implement as disguised P0 requirements)

- GHSL interpolation and residential-area subtraction algorithms.
- Dasymetric population redistribution or 23.5 m Level 2 modelling.
- Census-based scientific validation or claim of population prediction accuracy.
- Full end-user application, enterprise-grade RBAC, paid cloud/Kubernetes deployment, or unrequested production SLOs.
