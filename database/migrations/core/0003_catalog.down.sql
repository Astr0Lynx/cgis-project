-- Destroys all catalog rows. Refuses (no CASCADE) while geo.admin_units still references datasets.
DROP FUNCTION IF EXISTS catalog.datasets_intersecting(geometry, text);
DROP FUNCTION IF EXISTS catalog.datasets_in_bbox(double precision, double precision, double precision, double precision, integer, text);
DROP FUNCTION IF EXISTS catalog.resolve_dataset(text);
DROP FUNCTION IF EXISTS catalog.set_dataset_status(text, integer, text);
DROP FUNCTION IF EXISTS catalog.register_dataset(jsonb);
DROP VIEW IF EXISTS catalog.current_datasets;
DROP TABLE IF EXISTS catalog.datasets;
DROP FUNCTION IF EXISTS catalog.datasets_guard();
DROP SCHEMA IF EXISTS catalog;
