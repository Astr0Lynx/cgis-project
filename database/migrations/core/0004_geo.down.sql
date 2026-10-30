DROP FUNCTION IF EXISTS geo.summarize_units(geometry);
DROP FUNCTION IF EXISTS geo.unit_overlap(geometry, text);
DROP FUNCTION IF EXISTS geo.units_covered_by(geometry, text);
DROP FUNCTION IF EXISTS geo.units_intersecting(geometry, text);
DROP FUNCTION IF EXISTS geo.units_in_bbox(double precision, double precision, double precision, double precision, integer, text);
DROP FUNCTION IF EXISTS geo.upsert_admin_unit(text, text, text, geometry, jsonb, text, integer, text, jsonb, boolean);
DROP FUNCTION IF EXISTS geo.normalize_multipolygon(geometry, boolean);
DROP TABLE IF EXISTS geo.admin_units;
DROP SCHEMA IF EXISTS geo;
