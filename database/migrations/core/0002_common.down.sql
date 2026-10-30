DROP FUNCTION IF EXISTS cgis.touch_updated_at();
DROP FUNCTION IF EXISTS cgis.envelope_4326(double precision, double precision, double precision, double precision, integer);
DROP FUNCTION IF EXISTS cgis.to_4326(geometry);
DROP FUNCTION IF EXISTS cgis.srid_from_crs(text);
DROP FUNCTION IF EXISTS cgis.is_safe_relative_path(text);
DROP SCHEMA IF EXISTS cgis;
