-- TEST STUB ONLY - NOT A MIGRATION.
-- Minimal schema mirroring contracts/SHARED_CONTRACTS.md (frozen v1) so Agent 4's
-- services can be tested before Agent 2's real migrations are merged. Agent 2 owns
-- database/; Agent 6 runs the real schema during integration. Nothing here is used
-- outside ogc-services/tests/.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS geo;

CREATE TABLE catalog.datasets (
    dataset_id      TEXT NOT NULL,
    version         INTEGER NOT NULL CHECK (version > 0),
    title           TEXT NOT NULL CHECK (title <> ''),
    description     TEXT,
    kind            TEXT NOT NULL CHECK (kind IN ('raster', 'vector')),
    product_level   TEXT NOT NULL CHECK (product_level IN ('source', 'level1', 'level2', 'other')),
    year            INTEGER,
    crs             TEXT NOT NULL CHECK (crs ~ '^EPSG:[0-9]+$'),
    bbox            geometry(Polygon, 4326) NOT NULL,
    resolution_x    NUMERIC CHECK (resolution_x > 0),
    resolution_y    NUMERIC CHECK (resolution_y > 0),
    units           TEXT,
    nodata          NUMERIC,
    asset_path      TEXT,
    checksum_sha256 TEXT,
    source          TEXT,
    provenance      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL CHECK (status IN ('staged', 'ready', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, version)
);
CREATE INDEX datasets_bbox_gix ON catalog.datasets USING gist (bbox);

CREATE VIEW catalog.current_datasets AS
SELECT DISTINCT ON (dataset_id) *
FROM catalog.datasets
WHERE status = 'ready'
ORDER BY dataset_id, version DESC;

CREATE TABLE geo.admin_units (
    unit_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    level   TEXT,
    geom    geometry(MultiPolygon, 4326)
);
CREATE INDEX admin_units_geom_gix ON geo.admin_units USING gist (geom);

-- Fixture rows per contracts/SYNTHETIC_DATA.md (SYNTHETIC, deterministic-fixture).
INSERT INTO geo.admin_units (unit_id, name, level, geom) VALUES
  ('town-a', 'Town A', 'synthetic_town',
   ST_Multi(ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326))),
  ('town-b', 'Town B', 'synthetic_town',
   ST_Multi(ST_MakeEnvelope(78.030, 17.950, 78.035, 17.955, 4326)));

INSERT INTO catalog.datasets
  (dataset_id, version, title, description, kind, product_level, year, crs, bbox,
   resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance, status)
VALUES
  ('synthetic-towns', 1, 'SYNTHETIC towns (Town A, Town B)',
   'SYNTHETIC deterministic-fixture town polygons; not real boundaries.',
   'vector', 'source', NULL, 'EPSG:4326',
   ST_MakeEnvelope(78.010, 17.950, 78.035, 17.990, 4326),
   NULL, NULL, NULL, NULL, NULL, NULL, 'deterministic-fixture',
   '{"synthetic": true, "generator": "ogc-services test stub"}', 'ready'),
  ('synthetic-worldpop-2001', 1, 'SYNTHETIC population raster (2001)',
   'SYNTHETIC deterministic-fixture raster; value 1 per valid cell; not real population.',
   'raster', 'source', 2001, 'EPSG:4326',
   ST_MakeEnvelope(78.000, 17.936, 78.064, 18.000, 4326),
   0.001, 0.001, 'people_per_cell', -9999, 'synthetic/synthetic_2001.tif', NULL, 'deterministic-fixture',
   '{"synthetic": true, "generator": "ogc-services test stub"}', 'ready'),
  ('synthetic-worldpop-2011', 1, 'SYNTHETIC population raster (2011)',
   'SYNTHETIC deterministic-fixture raster; value 2 per valid cell; not real population.',
   'raster', 'source', 2011, 'EPSG:4326',
   ST_MakeEnvelope(78.000, 17.936, 78.064, 18.000, 4326),
   0.001, 0.001, 'people_per_cell', -9999, 'synthetic/synthetic_2011.tif', NULL, 'deterministic-fixture',
   '{"synthetic": true, "generator": "ogc-services test stub"}', 'ready');
