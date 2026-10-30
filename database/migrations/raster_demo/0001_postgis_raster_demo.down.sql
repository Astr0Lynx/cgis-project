DROP FUNCTION IF EXISTS raster_demo.zonal_stats(text, integer, geometry, boolean);
DROP FUNCTION IF EXISTS raster_demo.load_synthetic_fixture(text, integer, double precision, integer);
DROP TABLE IF EXISTS raster_demo.tiles;
DROP SCHEMA IF EXISTS raster_demo;
DROP EXTENSION IF EXISTS postgis_raster;
