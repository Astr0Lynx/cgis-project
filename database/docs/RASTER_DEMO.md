# OPTIONAL: PostGIS Raster demonstration

**Status: demonstration only.** Primary raster storage is Cloud-Optimized GeoTIFF files on the
`/data/assets` volume, read by TiTiler and the processing service through GDAL/rasterio. Nothing in
the core schema, and no service, depends on PostGIS Raster. The demo lives in its own migration
track (`migrations/raster_demo/`) and its own schema (`raster_demo`), so it can be installed and
removed on its own.

## Run it

```bash
python -m cgis_db migrate up                      # core first
python -m cgis_db raster-demo                     # applies raster_demo track, loads tiles, prints JSON
python -m cgis_db migrate down --track raster_demo --target 0   # remove it again
```

`raster_demo.load_synthetic_fixture(dataset_id, version, value, tile_size)` builds the SYNTHETIC grid
from `contracts/SYNTHETIC_DATA.md` **in SQL**: 64×64 cells, EPSG:4326, upper-left (78.000, 18.000),
0.001° pixels, NoData −9999 on the first 4 rows and first 4 columns, and every other cell = `value`.
It is cut into 16×16 tiles (16 tiles) with a GiST index on `ST_ConvexHull(rast)`. Generating the grid
in SQL keeps the database tests independent of Agent 3's generator. Agent 3's real fixture GeoTIFF
loads the same way:

```bash
raster2pgsql -s 4326 -t 16x16 -N -9999 -I -C -M synthetic_2001.tif raster_demo.import_2001 | psql
```

## Result (pixel-centre inclusion, `touched=false`)

`raster_demo.zonal_stats(dataset_id, version, aoi, touched)` runs `ST_Clip` on each intersecting
tile, then `ST_SummaryStatsAgg(..., exclude_nodata => true)`. Recorded output of
`python -m cgis_db raster-demo`:

| Dataset | AOI | count_valid | sum | mean | nodata_excluded |
|---|---|---:|---:|---:|---:|
| synthetic-worldpop-2001 | Town A | 100 | 100 | 1 | 0 |
| synthetic-worldpop-2011 | Town A | 100 | 200 | 2 | 0 |
| synthetic-worldpop-2001 | Town B | 25 | 25 | 1 | 0 |
| synthetic-worldpop-2011 | Town B | 25 | 50 | 2 | 0 |
| synthetic-worldpop-2001 | NoData-edge AOI (77.998–78.008, 17.992–18.002) | 16 | 16 | 1 | 48 |
| synthetic-worldpop-2001 | disjoint AOI (79.00–79.01, 19.00–19.01) | 0 | NULL | NULL | 0 |

These match the contract table in `SYNTHETIC_DATA.md`. In the NoData-edge case, 8×8 pixel centres
fall inside both the AOI and the raster. The NoData strip covers 48 of them, and those cells are
excluded and reported, never treated as zero. The part of the AOI outside the raster contributes
nothing.

## Semantics and limitations compared with GDAL/rasterio

| Aspect | PostGIS Raster (this demo) | GDAL / rasterio on COG (primary path) |
|---|---|---|
| Pixel selection | `ST_Clip(..., touched=false)` uses GDAL's rasterizer, so pixel-centre inclusion is the same as `rasterio.features.geometry_mask(all_touched=False)` | `all_touched=False` |
| Centre exactly on the AOI edge | Undefined and implementation-specific. An AOI with edges through pixel centres selected 80 pixels (10×8, asymmetric) | Same ambiguity. Tests use AOIs aligned to pixel *edges* |
| `touched=true` on **tiled** rasters | **Undercounts at tile seams.** The inset AOI selects 81 pixels per tile-wise clip, but 100 on `ST_Union` of the tiles. Do not use `touched=true` per tile for analytics | Consistent over the whole window |
| NoData | Per-band NoData value; `exclude_nodata_value=true` excludes it. NoData counts need a second clip of a 1-valued mask band, as done here | Masked arrays / dataset mask |
| CRS | Tiles keep their native SRID. The AOI is transformed to the raster SRID (vector side), and the raster is never warped | Same pattern: transform the AOI to the native CRS |
| Storage | Raster blobs inside the DB: backups, WAL and VACUUM grow with the data, and there is no HTTP range access for tilers | COG on disk/object store with HTTP range reads; TiTiler-friendly |
| Scale | Fine for small, tiled AOI analytics close to vector data | Preferred for large national rasters and tile serving |

**Conclusion:** PostGIS Raster reproduces the contract numbers and is useful for SQL-side
cross-checks of zonal statistics. For Phase 2, large GeoTIFFs should stay as COG files referenced by
`catalog.datasets.asset_path`, not as required DB blobs.
