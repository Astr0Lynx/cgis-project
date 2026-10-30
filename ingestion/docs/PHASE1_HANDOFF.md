# Phase 1 → Phase 2 data handoff: manifest and acceptance conditions

Phase 1 (data collection and WorldPop preprocessing) will eventually deliver real processed rasters and
boundary files. They go through **the same importer, unchanged code path**, as the synthetic fixtures.
This document says what a delivery must contain to be accepted. A template is in
[`../examples/manifest.phase1-template.json`](../examples/manifest.phase1-template.json).

## Delivery layout

```
<delivery>/
  manifest.json                # {"manifest_version": 1, "datasets": [...]}
  rasters/<name>.tif           # one file per dataset version
  vectors/<name>.geojson
  PROVENANCE.md                # optional human-readable notes
```

Mount or copy the delivery somewhere the ingestion container can read, for example
`/data/incoming/<delivery>`. Run `validate` first (it writes nothing), then `import`:

```bash
docker compose run --rm ingestion validate --manifest /data/incoming/<delivery>/manifest.json
docker compose run --rm ingestion import   --manifest /data/incoming/<delivery>/manifest.json
```

## Per-dataset manifest fields

| Field | Phase 1 guidance |
|---|---|
| `dataset_id` | Stable slug, e.g. `worldpop-telangana-2011`. Never reuse an ID for a different product. |
| `version` | Start at 1. **Any change to the file requires a new version**; the importer refuses to replace a ready version. |
| `title`, `description` | Human readable. Say what the product is, not what it might be used for. |
| `kind` | `raster` or `vector`. |
| `product_level` | `source` for processed WorldPop inputs. `level1` / `level2` are reserved for Phase 3 outputs. |
| `year` | The reference year of the product (integer). |
| `units` | Required for rasters. Use `people_per_cell` for count rasters. A density raster must say so (e.g. `people_per_km2`); Phase 2 never converts between them. |
| `source` | Upstream label, e.g. `WorldPop (processed by Phase 1)`. |
| `file` | Path relative to the manifest. |
| `checksum_sha256` | **Required by policy for real data** (optional in the schema). The importer verifies it. |
| `band` | Band to publish, default 1. |
| `expect` | **Recommended.** Assert `crs`, `nodata`, `dtype`, and optionally `width`/`height`/`resolution`/`bounds` so that a wrong file is rejected instead of silently published. |
| `provenance` | Object: upstream URL/DOI, license, retrieval date, Phase 1 code commit SHA, processing steps, resampling method. Stored verbatim in `catalog.datasets.provenance`. |
| vector `id_property` / `name_property` / `level_property` | Field names for the unit ID, display name and admin level. |
| vector `unit_id_prefix` | Strongly recommended, e.g. `tg-district-`. `geo.admin_units.unit_id` is a global key, and raw source codes from different boundary sets can collide. |

## Acceptance conditions (checked automatically)

A dataset is published (`status=ready`) only if all of these hold. Otherwise the CLI prints a
`failed` result with a specific `error.code`.

**Rasters**
- GDAL can read the file, and every block decodes (`corrupt_raster`). The requested band exists (`missing_band`).
- The CRS is present and resolves to an EPSG code (`missing_crs`, `unsupported_crs`). Geographic and
  projected CRSs are both accepted. The importer does not assume EPSG:4326, any resolution, or the
  synthetic grid.
- The geotransform is north-up, with no rotation or shear (`missing_transform`, `rotated_transform`, `invalid_transform`).
- NoData is declared and fits the dtype (`missing_nodata`, `invalid_nodata`). WorldPop products
  commonly use `-99999`; that value is read from the file. A file with no NoData is accepted only with
  `allow_missing_nodata: true`.
- No undeclared NaN/Inf cells, and at least one valid cell (`invalid_values`, `empty_raster`).
- Matches every `expect` assertion (`expectation_mismatch`, with per-field details).
- Matches `checksum_sha256` (`checksum_mismatch`).
- The produced COG passes `rio_cogeo` validation and is identical to the source pixel for pixel, with
  the same mask, CRS, transform, NoData, dtype and units.

**Vectors** (GeoJSON in v1)
- A FeatureCollection with at least one feature. Every geometry is a Polygon/MultiPolygon that is
  valid and non-empty (`invalid_geometry`, `unsupported_geometry`). Invalid geometries are **not**
  auto-repaired; fix them in Phase 1 (e.g. `ST_MakeValid` / `shapely.make_valid`) and document that in
  provenance.
- RFC 7946 lon/lat WGS84, or a legacy named `crs` member that is reprojected to EPSG:4326
  (`unsupported_crs`). Coordinates must fall in lon/lat range after reprojection (`invalid_coordinates`).
- Unique, non-empty unit IDs and names (`duplicate_unit_id`, `missing_unit_id`, `missing_name`).

## Not accepted in v1

- GeoPackage / Shapefile. Convert to GeoJSON (`ogr2ogr -f GeoJSON -t_srs EPSG:4326 out.geojson in.gpkg`).
  GPKG support can be added behind the same interface later.
- Rotated grids, GCP-only rasters, rasters without an EPSG-identifiable CRS.
- Multi-band publication. Each band is a separate dataset (select it with `band`).
- Very large rasters are supported, since all reads and writes are block-wise and BIGTIFF is used when
  needed. However, validation reads every pixel, so allow time proportional to file size.

## Phase 2 does NOT do

No interpolation to target years, no GHSL residential subtraction, no dasymetric redistribution, no
unit conversion, no resampling of the published asset (overviews are NEAREST and display-only). These
belong to Phase 3.
