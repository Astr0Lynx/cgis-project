-- Fails (by design, no CASCADE) if anything outside this project still depends on PostGIS.
DROP EXTENSION IF EXISTS postgis;
