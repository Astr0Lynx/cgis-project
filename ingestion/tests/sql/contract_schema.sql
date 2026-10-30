-- TEST-ONLY stand-in for Agent 2's migrations, written from the frozen v1 contract
-- (contracts/SHARED_CONTRACTS.md § Dataset catalog). It is NOT a migration and is never applied
-- outside the disposable test database. Constraints are deliberately strict (NOT NULL crs/bbox,
-- CHECKed enums, relative asset_path) so the importer is exercised against a worst-case schema.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS geo;

CREATE TABLE IF NOT EXISTS catalog.datasets (
    dataset_id      TEXT        NOT NULL CHECK (dataset_id <> ''),
    version         INTEGER     NOT NULL CHECK (version > 0),
    title           TEXT        NOT NULL CHECK (btrim(title) <> ''),
    description     TEXT,
    kind            TEXT        NOT NULL CHECK (kind IN ('raster', 'vector')),
    product_level   TEXT        NOT NULL CHECK (product_level IN ('source', 'level1', 'level2', 'other')),
    year            INTEGER,
    crs             TEXT        NOT NULL CHECK (crs ~ '^EPSG:[0-9]+$'),
    bbox            geometry(Polygon, 4326) NOT NULL,
    resolution_x    NUMERIC     CHECK (resolution_x > 0),
    resolution_y    NUMERIC     CHECK (resolution_y > 0),
    units           TEXT,
    nodata          NUMERIC,
    asset_path      TEXT        CHECK (asset_path !~ '^/' AND asset_path !~ '(^|/)\.\.(/|$)'),
    checksum_sha256 TEXT        NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    source          TEXT        NOT NULL,
    provenance      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT        NOT NULL CHECK (status IN ('staged', 'ready', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, version)
);
CREATE INDEX IF NOT EXISTS datasets_bbox_gist ON catalog.datasets USING gist (bbox);

CREATE OR REPLACE VIEW catalog.current_datasets AS
SELECT DISTINCT ON (dataset_id) *
FROM catalog.datasets
WHERE status = 'ready'
ORDER BY dataset_id, version DESC;

CREATE TABLE IF NOT EXISTS geo.admin_units (
    unit_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    level   TEXT,
    geom    geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_units_geom_gist ON geo.admin_units USING gist (geom);
