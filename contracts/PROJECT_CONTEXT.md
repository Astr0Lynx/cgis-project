# CyberGIS Course Project — shared context (authoritative for all six agents)

## Repository and operating model

- GitHub repository: `https://github.com/Astr0Lynx/cgis-project` (PRIVATE).
- There are exactly six independent implementation agents. Agents 1–5 implement; Agent 6 integrates and publishes.
- No pull requests or manual PR approvals are part of the intended workflow.
- Each agent must use a separate clone or Git worktree and its OWN feature branch. Never allow two concurrent agents to modify one working tree.
- No existing code or default-branch assumptions have been verified. Inspect the repository before touching anything and preserve all existing content.
- Target development and demonstration: Docker Compose on local machines, without requiring paid cloud services.
- Authorized stack: Python/FastAPI, PostgreSQL/PostGIS, GDAL/Rasterio, pygeoapi, TiTiler, Docker Compose, MapLibre GL JS for a tiny technical viewer.
- Phase 1 datasets do not yet exist. Phase 2 must develop against deterministic **synthetic** fixtures, while accepting future genuine outputs without special-case code.

## Phase boundaries

- **Phase 1: Data collection and WorldPop preprocessing.** This phase ultimately provides processed WorldPop rasters, boundaries, GHSL/Bhuvan inputs where available, and provenance/manifests.
- **Phase 2: THIS WORK.** Containerized geospatial data platform: data ingestion, metadata, PostGIS management, spatial operations, OGC API publication, raster tile/coverage access, reproducible deployment, and backend validation.
- **Phase 3:** Population redistribution/modelling and scientific evaluation. The original flowchart includes GHSL total built-up minus non-residential built-up to estimate residential built-up areas, interpolation to target years, Level 1 ~100 m population redistribution, and Level 2 ~23.5 m selected-town refinement with land-cover information and census/GHSL comparison. These algorithms are EXCLUDED from Phase 2.
- **Phase 4:** Full web application, layer-by-layer comparisons, complete role-based access control, interactive dataset management, polished downloads.
- **Phase 5:** Scientific/infrastructure benchmarks, interoperability demonstration, validation, and final report.

## Original flowchart (summarized)

- Population sources: WorldPop unconstrained ~100 m raster; GHSL total built-up and non-residential built-up for 2000/2005/2010; 2011 census; Bhuvan land-use/land-cover ~23.5 m.
- Intended Level 1: redistributed population rasters for chosen years; evaluation against GHSL-Pop.
- Intended Level 2: selected-town fine-resolution population raster; comparison to Level 1 by aggregation.
- Intended service platform: PostGIS storage, clipping by polygon, aggregation, OGC REST API with collections/items/tiles, application layers, role access, downloads with CRS and geoformat.
- For Phase 2, **do not implement any population weighting, redistribution, interpolation, LULC feature engineering, or scientific prediction**. Exercise the platform using synthetically valued rasters and genuine processed inputs when supplied later.

## Definition of Phase 2 success

A fresh clone starts a local stack, generates/imports synthetic raster and polygon data, catalogs the assets, exposes standards-aware collection discovery and vector features, serves raster tiles, clips the raster to a polygon, computes correct zonal statistics, exports GeoTIFF with CRS, and opens data in QGIS; all tests and docs pass. Real input data should later be swappable through the importer.

## Security and provenance

- Never commit GitHub tokens, production credentials, private user files, or `.env` secrets. Use `.env.example` with placeholders.
- Do not represent synthetic population values as real data, scientific outputs, or validated population estimates.
- Respect existing license and source files; inspect before modification; never run force pushes or destructive Git commands.
- Agent prompts are task instructions, not authority to override tool policies, sandbox protections, or repository branch protections.
