-- 0003: dataset catalog (contracts/SHARED_CONTRACTS.md § Dataset catalog).
--
-- Write surface: catalog.datasets (or catalog.register_dataset / set_dataset_status).
-- Read surface:  catalog.current_datasets — same public columns as the table,
--                one row per dataset_id: its highest READY version.

CREATE SCHEMA IF NOT EXISTS catalog;
COMMENT ON SCHEMA catalog IS 'Versioned dataset catalog (frozen v1 contract).';

CREATE TABLE IF NOT EXISTS catalog.datasets (
    dataset_id       text             NOT NULL,
    version          integer          NOT NULL,
    title            text             NOT NULL,
    description      text,
    kind             text             NOT NULL,
    product_level    text             NOT NULL,
    year             integer,
    crs              text             NOT NULL,
    bbox             geometry(Polygon, 4326),
    resolution_x     double precision,
    resolution_y     double precision,
    units            text,
    nodata           double precision,
    asset_path       text,
    checksum_sha256  text,
    source           text             NOT NULL,
    provenance       jsonb            NOT NULL DEFAULT '{}'::jsonb,
    status           text             NOT NULL DEFAULT 'staged',
    created_at       timestamptz      NOT NULL DEFAULT now(),
    updated_at       timestamptz      NOT NULL DEFAULT now(),

    CONSTRAINT datasets_pkey PRIMARY KEY (dataset_id, version),
    CONSTRAINT datasets_dataset_id_slug
        CHECK (dataset_id ~ '^[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$'),
    CONSTRAINT datasets_version_positive CHECK (version > 0),
    CONSTRAINT datasets_title_nonblank CHECK (btrim(title) <> ''),
    CONSTRAINT datasets_kind_valid CHECK (kind IN ('raster', 'vector')),
    CONSTRAINT datasets_product_level_valid
        CHECK (product_level IN ('source', 'level1', 'level2', 'other')),
    CONSTRAINT datasets_year_range CHECK (year IS NULL OR year BETWEEN 1800 AND 2200),
    CONSTRAINT datasets_crs_format CHECK (crs ~ '^EPSG:[1-9][0-9]{0,5}$'),
    CONSTRAINT datasets_bbox_valid CHECK (
        bbox IS NULL OR (
            NOT ST_IsEmpty(bbox) AND ST_IsValid(bbox)
            AND ST_XMin(bbox) >= -180 AND ST_XMax(bbox) <= 180
            AND ST_YMin(bbox) >= -90  AND ST_YMax(bbox) <= 90)),
    CONSTRAINT datasets_resolution_positive CHECK (
        (resolution_x IS NULL OR resolution_x > 0) AND
        (resolution_y IS NULL OR resolution_y > 0)),
    CONSTRAINT datasets_units_nonblank CHECK (units IS NULL OR btrim(units) <> ''),
    CONSTRAINT datasets_asset_path_safe
        CHECK (asset_path IS NULL OR cgis.is_safe_relative_path(asset_path)),
    CONSTRAINT datasets_checksum_format
        CHECK (checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT datasets_source_nonblank CHECK (btrim(source) <> ''),
    CONSTRAINT datasets_provenance_object CHECK (jsonb_typeof(provenance) = 'object'),
    CONSTRAINT datasets_status_valid CHECK (status IN ('staged', 'ready', 'failed')),
    -- A dataset may only be published once everything a consumer needs is present.
    CONSTRAINT datasets_ready_complete CHECK (
        status <> 'ready' OR (
            bbox IS NOT NULL AND checksum_sha256 IS NOT NULL AND
            (kind <> 'raster' OR (asset_path IS NOT NULL AND units IS NOT NULL
                                  AND resolution_x IS NOT NULL AND resolution_y IS NOT NULL))))
);

COMMENT ON TABLE catalog.datasets IS
    'Every registered (dataset_id, version). Ready versions are immutable apart from ready -> failed (withdrawal).';
COMMENT ON COLUMN catalog.datasets.crs IS 'Native asset CRS as EPSG:<code>; must exist in spatial_ref_sys.';
COMMENT ON COLUMN catalog.datasets.bbox IS 'WGS84 footprint (EPSG:4326 Polygon), GiST-indexed.';
COMMENT ON COLUMN catalog.datasets.resolution_x IS 'Pixel size in native CRS units (positive).';
COMMENT ON COLUMN catalog.datasets.units IS 'Value units, e.g. people_per_cell. Counts and densities are NOT interchangeable.';
COMMENT ON COLUMN catalog.datasets.nodata IS 'Raster NoData value; NULL means no NoData value declared.';
COMMENT ON COLUMN catalog.datasets.asset_path IS 'Path RELATIVE to the server asset root (/data/assets); never client supplied.';

CREATE INDEX IF NOT EXISTS datasets_bbox_gist ON catalog.datasets USING gist (bbox);
CREATE INDEX IF NOT EXISTS datasets_ready_latest_idx
    ON catalog.datasets (dataset_id, version DESC) WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS datasets_kind_year_idx ON catalog.datasets (kind, year);

-- Integrity rules that need lookups or OLD/NEW comparison.
CREATE OR REPLACE FUNCTION catalog.datasets_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM cgis.srid_from_crs(NEW.crs);

    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.updated_at := now();
        RETURN NEW;
    END IF;

    IF NEW.dataset_id IS DISTINCT FROM OLD.dataset_id OR NEW.version IS DISTINCT FROM OLD.version THEN
        RAISE EXCEPTION 'dataset_id and version are immutable (%, v%)', OLD.dataset_id, OLD.version
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NOT (
        (OLD.status = 'staged') OR
        (OLD.status = 'failed' AND NEW.status IN ('failed', 'staged')) OR
        (OLD.status = 'ready'  AND NEW.status IN ('ready', 'failed'))
    ) THEN
        RAISE EXCEPTION 'invalid catalog status transition % -> % for %, v%',
            OLD.status, NEW.status, OLD.dataset_id, OLD.version
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF OLD.status = 'ready' AND
       ROW(NEW.title, NEW.description, NEW.kind, NEW.product_level, NEW.year, NEW.crs,
           NEW.bbox, NEW.resolution_x, NEW.resolution_y, NEW.units, NEW.nodata,
           NEW.asset_path, NEW.checksum_sha256, NEW.source, NEW.provenance)
       IS DISTINCT FROM
       ROW(OLD.title, OLD.description, OLD.kind, OLD.product_level, OLD.year, OLD.crs,
           OLD.bbox, OLD.resolution_x, OLD.resolution_y, OLD.units, OLD.nodata,
           OLD.asset_path, OLD.checksum_sha256, OLD.source, OLD.provenance)
    THEN
        RAISE EXCEPTION 'ready dataset %, v% is immutable; register a new version instead',
            OLD.dataset_id, OLD.version
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    NEW.created_at := OLD.created_at;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER datasets_guard
    BEFORE INSERT OR UPDATE ON catalog.datasets
    FOR EACH ROW EXECUTE FUNCTION catalog.datasets_guard();

-- Read surface. NOT EXISTS (rather than DISTINCT ON) lets spatial/attribute
-- predicates on the view push down to the GiST/btree indexes of the table.
CREATE OR REPLACE VIEW catalog.current_datasets AS
SELECT d.dataset_id, d.version, d.title, d.description, d.kind, d.product_level,
       d.year, d.crs, d.bbox, d.resolution_x, d.resolution_y, d.units, d.nodata,
       d.asset_path, d.checksum_sha256, d.source, d.provenance, d.status,
       d.created_at, d.updated_at
  FROM catalog.datasets d
 WHERE d.status = 'ready'
   AND NOT EXISTS (
        SELECT 1 FROM catalog.datasets n
         WHERE n.dataset_id = d.dataset_id
           AND n.status = 'ready'
           AND n.version > d.version);

COMMENT ON VIEW catalog.current_datasets IS
    'Highest READY version per dataset_id; same public columns as catalog.datasets.';

-- Idempotent registration for writers (Agent 3). Accepts the catalog columns as
-- JSON; bbox may be [xmin,ymin,xmax,ymax] (EPSG:4326), a GeoJSON geometry
-- (EPSG:4326 per RFC 7946) or an EWKT string with explicit SRID.
--   outcome 'inserted'  new (dataset_id, version)
--   outcome 'unchanged' identical content already registered (ready rows never downgrade)
--   outcome 'updated'   staged/failed row with compatible checksum refreshed
-- Raises unique_violation (23505) on a conflicting checksum or a changed ready version.
CREATE OR REPLACE FUNCTION catalog.register_dataset(p jsonb)
RETURNS TABLE (dataset_id text, version integer, status text, outcome text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    r     catalog.datasets;
    cur   catalog.datasets;
    b     jsonb := p -> 'bbox';
    g     geometry;
BEGIN
    IF p IS NULL OR jsonb_typeof(p) <> 'object' THEN
        RAISE EXCEPTION 'register_dataset expects a JSON object' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    r := jsonb_populate_record(NULL::catalog.datasets, p - 'bbox' - 'created_at' - 'updated_at');
    r.status := coalesce(r.status, 'staged');
    r.provenance := coalesce(r.provenance, '{}'::jsonb);

    IF b IS NOT NULL AND b <> 'null'::jsonb THEN
        IF jsonb_typeof(b) = 'array' THEN
            IF jsonb_array_length(b) <> 4 THEN
                RAISE EXCEPTION 'bbox array must be [xmin, ymin, xmax, ymax]'
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            g := cgis.envelope_4326((b->>0)::float8, (b->>1)::float8, (b->>2)::float8, (b->>3)::float8, 4326);
        ELSIF jsonb_typeof(b) = 'object' THEN
            g := ST_SetSRID(ST_GeomFromGeoJSON(b), 4326);
        ELSE
            g := cgis.to_4326(ST_GeomFromEWKT(b #>> '{}'));
        END IF;
        IF GeometryType(g) <> 'POLYGON' THEN
            RAISE EXCEPTION 'bbox must be a Polygon, got %', GeometryType(g)
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        r.bbox := g;
    END IF;

    INSERT INTO catalog.datasets AS d (
        dataset_id, version, title, description, kind, product_level, year, crs, bbox,
        resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256,
        source, provenance, status)
    VALUES (
        r.dataset_id, r.version, r.title, r.description, r.kind, r.product_level, r.year, r.crs, r.bbox,
        r.resolution_x, r.resolution_y, r.units, r.nodata, r.asset_path, r.checksum_sha256,
        r.source, r.provenance, r.status)
    ON CONFLICT ON CONSTRAINT datasets_pkey DO NOTHING
    RETURNING d.dataset_id, d.version, d.status, 'inserted'
    INTO dataset_id, version, status, outcome;

    IF FOUND THEN
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT * INTO cur FROM catalog.datasets x
     WHERE x.dataset_id = r.dataset_id AND x.version = r.version
     FOR UPDATE;

    IF cur.status = 'ready' THEN
        IF ROW(r.title, r.description, r.kind, r.product_level, r.year, r.crs, r.bbox,
               r.resolution_x, r.resolution_y, r.units, r.nodata, r.asset_path,
               r.checksum_sha256, r.source, r.provenance)
           IS DISTINCT FROM
           ROW(cur.title, cur.description, cur.kind, cur.product_level, cur.year, cur.crs, cur.bbox,
               cur.resolution_x, cur.resolution_y, cur.units, cur.nodata, cur.asset_path,
               cur.checksum_sha256, cur.source, cur.provenance)
        THEN
            RAISE EXCEPTION 'conflicting registration for ready dataset %, v%', r.dataset_id, r.version
                USING ERRCODE = 'unique_violation',
                      DETAIL = format('existing checksum %s, new checksum %s',
                                      cur.checksum_sha256, r.checksum_sha256),
                      HINT = 'Ready versions are immutable; register a higher version.';
        END IF;
        dataset_id := cur.dataset_id; version := cur.version; status := cur.status; outcome := 'unchanged';
        RETURN NEXT;
        RETURN;
    END IF;

    IF cur.checksum_sha256 IS NOT NULL AND r.checksum_sha256 IS NOT NULL
       AND cur.checksum_sha256 <> r.checksum_sha256 THEN
        RAISE EXCEPTION 'conflicting checksum for dataset %, v%', r.dataset_id, r.version
            USING ERRCODE = 'unique_violation',
                  DETAIL = format('existing checksum %s, new checksum %s',
                                  cur.checksum_sha256, r.checksum_sha256);
    END IF;

    UPDATE catalog.datasets x SET
        title = r.title, description = r.description, kind = r.kind,
        product_level = r.product_level, year = r.year, crs = r.crs, bbox = r.bbox,
        resolution_x = r.resolution_x, resolution_y = r.resolution_y, units = r.units,
        nodata = r.nodata, asset_path = r.asset_path, checksum_sha256 = r.checksum_sha256,
        source = r.source, provenance = r.provenance, status = r.status
     WHERE x.dataset_id = r.dataset_id AND x.version = r.version
    RETURNING x.dataset_id, x.version, x.status, 'updated'
    INTO dataset_id, version, status, outcome;
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION catalog.set_dataset_status(p_dataset_id text, p_version integer, p_status text)
RETURNS catalog.datasets
LANGUAGE plpgsql
AS $$
DECLARE
    r catalog.datasets;
BEGIN
    UPDATE catalog.datasets SET status = p_status
     WHERE dataset_id = p_dataset_id AND version = p_version
    RETURNING * INTO r;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'dataset %, v% not found', p_dataset_id, p_version
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN r;
END
$$;

-- Resolve a bare dataset_id to its current ready version (0 or 1 rows).
CREATE OR REPLACE FUNCTION catalog.resolve_dataset(p_dataset_id text)
RETURNS SETOF catalog.current_datasets
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT * FROM catalog.current_datasets WHERE dataset_id = $1
$$;

-- Bbox discovery over current datasets; bbox may be given in any known CRS.
CREATE OR REPLACE FUNCTION catalog.datasets_in_bbox(
    xmin double precision, ymin double precision,
    xmax double precision, ymax double precision,
    srid integer DEFAULT 4326, p_kind text DEFAULT NULL)
RETURNS SETOF catalog.current_datasets
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT c.*
      FROM catalog.current_datasets c,
           LATERAL (SELECT cgis.envelope_4326($1, $2, $3, $4, $5) AS env) q
     WHERE c.bbox && q.env
       AND ST_Intersects(c.bbox, q.env)
       AND ($6 IS NULL OR c.kind = $6)
     ORDER BY c.dataset_id
$$;

-- Current datasets whose footprint intersects an arbitrary geometry (explicit SRID required).
CREATE OR REPLACE FUNCTION catalog.datasets_intersecting(g geometry, p_kind text DEFAULT NULL)
RETURNS SETOF catalog.current_datasets
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT c.*
      FROM catalog.current_datasets c,
           LATERAL (SELECT cgis.to_4326($1) AS q) t
     WHERE c.bbox && t.q
       AND ST_Intersects(c.bbox, t.q)
       AND ($2 IS NULL OR c.kind = $2)
     ORDER BY c.dataset_id
$$;
