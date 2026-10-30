# Deterministic synthetic fixture specification (NOT real population)

There are no provided Phase 1 datasets. Agent 3 generates these files; all other agents may create lightweight test stubs strictly following this contract until generator is merged. Do not download or represent actual WorldPop data as part of this task.

## Raster geometry and values

- Two single-band GeoTIFF inputs: `synthetic_2001.tif`, `synthetic_2011.tif`.
- Grid: 64 columns × 64 rows, EPSG:4326; geotransform upper-left `(78.000, 18.000)`, pixel sizes `(+0.001, -0.001)` degrees; bounds west=78.000, east=78.064, south=17.936, north=18.000.
- 2001: value `1` in every valid cell; 2011: value `2` in every valid cell. Unit is synthetic `people_per_cell` (not density); years are metadata labels, not historical estimates.
- NoData: `-9999`, assigned to first 4 rows and first 4 columns. Valid values must be disjoint from NoData.
- COG derivatives should preserve spatial transform, actual per-pixel values, mask, NoData, band count, and units. Use lossless compression.
- All fixture IDs, titles, and provenance must say `SYNTHETIC` or `deterministic-fixture` conspicuously.

## Vector geometry

Create `synthetic_towns.geojson` FeatureCollection, EPSG:4326, with MultiPolygon-normalizable features:

- `town-a`: rectangle lon 78.010–78.020, lat 17.980–17.990; aligned to 10×10 valid raster cells.
- `town-b`: rectangle lon 78.030–78.035, lat 17.950–17.955; aligned to 5×5 valid raster cells.

Properties: `unit_id`, `name`, `level=synthetic_town`, `source=deterministic-fixture`; names `Town A`, `Town B`.

## Expected calculations

With **pixel-center inclusion** and no resampling:

| AOI | Valid pixels | 2001 sum | 2011 sum | 2001 mean | 2011 mean |
|---|---:|---:|---:|---:|---:|
| Town A | 100 | 100 | 200 | 1 | 2 |
| Town B | 25 | 25 | 50 | 1 | 2 |

Use a consistent polygon inclusion policy (`all_touched=false` / pixel center) for analytical tests and document how a visualization clip may differ. Add a separate AOI overlapping the NoData strip to test missing data exclusion, and a completely disjoint AOI for no-overlap errors. Beware floating point boundaries when computing windows: tests should verify values, not rely on exact byte-identical TIFF files.

## Provenance and packaging

- Agent 3 owns generator, idempotent fixtures manifest, and source code. Prefer committed code generating small binary test files rather than committing huge rasters.
- Seed command must be wired into the Phase 2 demo (through Makefile or Compose), documented by Agent 1 and Agent 6.
- Future real Phase 1 inputs may be in native geographic or projected CRS. The importer must not assume the synthetic EPSG:4326 grid.
- No service may hardcode the two synthetic IDs in core logic; tests may reference them.
