-- OPTIONAL demonstration track (python -m cgis_db migrate up --track raster_demo).
-- Primary raster storage is COG files on the asset volume; nothing in the core
-- schema depends on PostGIS Raster. This schema holds SMALL in-database tiles
-- to compare ST_Clip/ST_SummaryStatsAgg semantics with GDAL/rasterio.

CREATE EXTENSION IF NOT EXISTS postgis_raster;

CREATE SCHEMA IF NOT EXISTS raster_demo;
COMMENT ON SCHEMA raster_demo IS
    'OPTIONAL PostGIS Raster demonstration. Not a primary store; not read by services.';

CREATE TABLE IF NOT EXISTS raster_demo.tiles (
    tile_id     bigint   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id  text     NOT NULL,
    version     integer  NOT NULL CHECK (version > 0),
    rast        raster   NOT NULL,
    CONSTRAINT tiles_srid_known CHECK (ST_SRID(rast) > 0),
    CONSTRAINT tiles_single_band CHECK (ST_NumBands(rast) = 1)
);
COMMENT ON TABLE raster_demo.tiles IS
    'Tiled single-band rasters keyed by catalog (dataset_id, version); not FK-bound so demos can run before ingestion.';

CREATE INDEX IF NOT EXISTS tiles_dataset_idx ON raster_demo.tiles (dataset_id, version);
CREATE INDEX IF NOT EXISTS tiles_hull_gist ON raster_demo.tiles USING gist (ST_ConvexHull(rast));

-- Builds the SYNTHETIC fixture grid from contracts/SYNTHETIC_DATA.md in SQL:
-- 64x64, EPSG:4326, UL (78.000, 18.000), pixel 0.001 deg, NoData -9999 on the
-- first 4 rows and first 4 columns, every other cell = p_value. Re-running
-- replaces the tiles of (p_dataset_id, p_version).
CREATE OR REPLACE FUNCTION raster_demo.load_synthetic_fixture(
    p_dataset_id text, p_version integer, p_value double precision, p_tile_size integer DEFAULT 16)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    r raster;
    n integer;
BEGIN
    IF p_value = -9999 THEN
        RAISE EXCEPTION 'fixture value must differ from NoData (-9999)' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    r := ST_MakeEmptyRaster(64, 64, 78.000, 18.000, 0.001, -0.001, 0, 0, 4326);
    r := ST_AddBand(r, '32BF'::text, p_value, -9999);
    r := ST_SetValues(r, 1, 1, 1, 64, 4, -9999::double precision, false);  -- first 4 rows
    r := ST_SetValues(r, 1, 1, 1, 4, 64, -9999::double precision, false);  -- first 4 columns

    DELETE FROM raster_demo.tiles WHERE dataset_id = p_dataset_id AND version = p_version;
    INSERT INTO raster_demo.tiles (dataset_id, version, rast)
    SELECT p_dataset_id, p_version, t FROM ST_Tile(r, p_tile_size, p_tile_size) AS t;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END
$$;

-- Zonal statistics over an AOI with explicit SRID (transformed to the raster CRS).
-- p_touched = false: pixel-centre inclusion (same as GDAL/rasterio all_touched=False).
-- NoData pixels are excluded from count/sum/mean and reported in nodata_excluded.
CREATE OR REPLACE FUNCTION raster_demo.zonal_stats(
    p_dataset_id text, p_version integer, p_aoi geometry, p_touched boolean DEFAULT false)
RETURNS TABLE (count_valid bigint, sum double precision, mean double precision,
               nodata_excluded bigint, pixels_selected bigint)
LANGUAGE plpgsql STABLE
AS $$
#variable_conflict use_column
DECLARE
    v_srid integer;
    aoi geometry;
BEGIN
    SELECT ST_SRID(rast) INTO v_srid FROM raster_demo.tiles
     WHERE dataset_id = p_dataset_id AND version = p_version LIMIT 1;
    IF v_srid IS NULL THEN
        RAISE EXCEPTION 'no raster tiles for %, v%', p_dataset_id, p_version USING ERRCODE = 'no_data_found';
    END IF;
    aoi := cgis.to_4326(p_aoi);  -- rejects SRID 0 / unknown SRIDs
    IF NOT ST_IsValid(aoi) OR GeometryType(aoi) NOT IN ('POLYGON', 'MULTIPOLYGON') THEN
        RAISE EXCEPTION 'AOI must be a valid Polygon/MultiPolygon' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    aoi := ST_Transform(aoi, v_srid);

    RETURN QUERY
    WITH hit AS (
        SELECT t.rast FROM raster_demo.tiles t
         WHERE t.dataset_id = p_dataset_id AND t.version = p_version
           AND ST_ConvexHull(t.rast) && aoi
           AND ST_Intersects(ST_ConvexHull(t.rast), aoi)),
    stats AS (
        SELECT ST_SummaryStatsAgg(ST_Clip(h.rast, 1, aoi, true, p_touched), 1, true, 1) AS s
          FROM hit h),
    -- Pixels selected by the SAME ST_Clip rule, counted on an all-ones mask
    -- raster with the tile's georeference (so NoData cells are counted too).
    sel AS (
        SELECT coalesce(sum(ST_Count(
                   ST_Clip(ST_AddBand(ST_MakeEmptyRaster(h.rast), '8BUI'::text, 1, 0),
                           1, aoi, true, p_touched), 1, true)), 0)::bigint AS n
          FROM hit h)
    SELECT coalesce((stats.s).count, 0)::bigint,
           CASE WHEN coalesce((stats.s).count, 0) > 0 THEN (stats.s).sum END,
           CASE WHEN coalesce((stats.s).count, 0) > 0 THEN (stats.s).mean END,
           sel.n - coalesce((stats.s).count, 0),
           sel.n
      FROM stats, sel;
END
$$;
