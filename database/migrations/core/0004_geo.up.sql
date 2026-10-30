-- 0004: administrative / analysis units (contracts/SHARED_CONTRACTS.md § geo).
--
-- Contract columns: unit_id, name, level, geom geometry(MultiPolygon,4326).
-- Additional columns keep imported attributes and provenance.

CREATE SCHEMA IF NOT EXISTS geo;
COMMENT ON SCHEMA geo IS 'Vector analysis units (towns, districts, ...) in EPSG:4326.';

CREATE TABLE IF NOT EXISTS geo.admin_units (
    unit_id          text         NOT NULL,
    name             text         NOT NULL,
    level            text,
    geom             geometry(MultiPolygon, 4326) NOT NULL,
    properties       jsonb        NOT NULL DEFAULT '{}'::jsonb,
    dataset_id       text,
    dataset_version  integer,
    source           text,
    provenance       jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT admin_units_pkey PRIMARY KEY (unit_id),
    CONSTRAINT admin_units_unit_id_nonblank CHECK (btrim(unit_id) <> ''),
    CONSTRAINT admin_units_name_nonblank CHECK (btrim(name) <> ''),
    CONSTRAINT admin_units_level_nonblank CHECK (level IS NULL OR btrim(level) <> ''),
    CONSTRAINT admin_units_geom_valid CHECK (
        NOT ST_IsEmpty(geom) AND ST_IsValid(geom) AND ST_NDims(geom) = 2
        AND ST_XMin(geom) >= -180 AND ST_XMax(geom) <= 180
        AND ST_YMin(geom) >= -90  AND ST_YMax(geom) <= 90),
    CONSTRAINT admin_units_properties_object CHECK (jsonb_typeof(properties) = 'object'),
    CONSTRAINT admin_units_provenance_object CHECK (jsonb_typeof(provenance) = 'object'),
    CONSTRAINT admin_units_dataset_ref_pair CHECK ((dataset_id IS NULL) = (dataset_version IS NULL)),
    CONSTRAINT admin_units_dataset_fkey FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES catalog.datasets (dataset_id, version) ON UPDATE RESTRICT ON DELETE RESTRICT
);

COMMENT ON TABLE geo.admin_units IS
    'Analysis units. geom is a valid 2D MultiPolygon in EPSG:4326; write through geo.upsert_admin_unit to normalize.';
COMMENT ON COLUMN geo.admin_units.properties IS 'Imported source attributes, preserved verbatim.';
COMMENT ON COLUMN geo.admin_units.dataset_id IS 'Catalog dataset (with dataset_version) this unit was imported from, if any.';

CREATE INDEX IF NOT EXISTS admin_units_geom_gist ON geo.admin_units USING gist (geom);
CREATE INDEX IF NOT EXISTS admin_units_level_idx ON geo.admin_units (level);
CREATE INDEX IF NOT EXISTS admin_units_dataset_idx ON geo.admin_units (dataset_id, dataset_version);

CREATE OR REPLACE TRIGGER admin_units_touch
    BEFORE INSERT OR UPDATE ON geo.admin_units
    FOR EACH ROW EXECUTE FUNCTION cgis.touch_updated_at();

-- Any areal geometry with an explicit SRID -> valid 2D MultiPolygon in EPSG:4326.
-- Invalid input is rejected unless p_repair is true, in which case ST_MakeValid
-- is applied and only the polygonal part is kept (documented, lossy).
CREATE OR REPLACE FUNCTION geo.normalize_multipolygon(g geometry, p_repair boolean DEFAULT false)
RETURNS geometry
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
    out geometry;
BEGIN
    IF g IS NULL OR ST_IsEmpty(g) THEN
        RAISE EXCEPTION 'geometry is null or empty' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF GeometryType(g) NOT IN ('POLYGON', 'MULTIPOLYGON') THEN
        RAISE EXCEPTION 'expected Polygon or MultiPolygon, got %', GeometryType(g)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    out := ST_Force2D(g);
    IF NOT ST_IsValid(out) THEN
        IF NOT p_repair THEN
            RAISE EXCEPTION 'invalid geometry: %', ST_IsValidReason(out)
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        out := ST_CollectionExtract(ST_MakeValid(out), 3);
        IF ST_IsEmpty(out) THEN
            RAISE EXCEPTION 'geometry has no polygonal area after repair'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;

    out := ST_Multi(cgis.to_4326(out));
    -- Reprojection can (rarely) introduce self-intersections.
    IF NOT ST_IsValid(out) THEN
        IF NOT p_repair THEN
            RAISE EXCEPTION 'geometry invalid after transformation to EPSG:4326: %', ST_IsValidReason(out)
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        out := ST_Multi(ST_CollectionExtract(ST_MakeValid(out), 3));
    END IF;
    RETURN out;
END
$$;

-- Normalizing upsert. A unit_id already owned by a different catalog dataset is
-- never silently overwritten.
CREATE OR REPLACE FUNCTION geo.upsert_admin_unit(
    p_unit_id text,
    p_name text,
    p_level text,
    p_geom geometry,
    p_properties jsonb DEFAULT '{}'::jsonb,
    p_dataset_id text DEFAULT NULL,
    p_dataset_version integer DEFAULT NULL,
    p_source text DEFAULT NULL,
    p_provenance jsonb DEFAULT '{}'::jsonb,
    p_repair boolean DEFAULT false)
RETURNS geo.admin_units
LANGUAGE plpgsql
AS $$
DECLARE
    r geo.admin_units;
BEGIN
    INSERT INTO geo.admin_units AS u (unit_id, name, level, geom, properties,
                                      dataset_id, dataset_version, source, provenance)
    VALUES (p_unit_id, p_name, p_level, geo.normalize_multipolygon(p_geom, p_repair),
            coalesce(p_properties, '{}'::jsonb), p_dataset_id, p_dataset_version,
            p_source, coalesce(p_provenance, '{}'::jsonb))
    ON CONFLICT ON CONSTRAINT admin_units_pkey DO UPDATE SET
        name = EXCLUDED.name, level = EXCLUDED.level, geom = EXCLUDED.geom,
        properties = EXCLUDED.properties, dataset_id = EXCLUDED.dataset_id,
        dataset_version = EXCLUDED.dataset_version, source = EXCLUDED.source,
        provenance = EXCLUDED.provenance
     WHERE u.dataset_id IS NULL OR u.dataset_id = EXCLUDED.dataset_id
    RETURNING * INTO r;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'unit_id "%" already belongs to another dataset', p_unit_id
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN r;
END
$$;

-- Units intersecting a bbox given in any known CRS (OGC bbox semantics: intersects).
CREATE OR REPLACE FUNCTION geo.units_in_bbox(
    xmin double precision, ymin double precision,
    xmax double precision, ymax double precision,
    srid integer DEFAULT 4326, p_level text DEFAULT NULL)
RETURNS SETOF geo.admin_units
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT u.*
      FROM geo.admin_units u,
           LATERAL (SELECT cgis.envelope_4326($1, $2, $3, $4, $5) AS env) q
     WHERE u.geom && q.env
       AND ST_Intersects(u.geom, q.env)
       AND ($6 IS NULL OR u.level = $6)
     ORDER BY u.unit_id
$$;

-- Units intersecting an arbitrary geometry. The QUERY geometry is transformed
-- to EPSG:4326 (never the indexed column), so the GiST index stays usable.
CREATE OR REPLACE FUNCTION geo.units_intersecting(g geometry, p_level text DEFAULT NULL)
RETURNS SETOF geo.admin_units
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT u.*
      FROM geo.admin_units u,
           LATERAL (SELECT cgis.to_4326($1) AS q) t
     WHERE u.geom && t.q
       AND ST_Intersects(u.geom, t.q)
       AND ($2 IS NULL OR u.level = $2)
     ORDER BY u.unit_id
$$;

-- Polygon-based selection: units entirely covered by the AOI.
CREATE OR REPLACE FUNCTION geo.units_covered_by(aoi geometry, p_level text DEFAULT NULL)
RETURNS SETOF geo.admin_units
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT u.*
      FROM geo.admin_units u,
           LATERAL (SELECT cgis.to_4326($1) AS q) t
     WHERE u.geom @ t.q
       AND ST_CoveredBy(u.geom, t.q)
       AND ($2 IS NULL OR u.level = $2)
     ORDER BY u.unit_id
$$;

-- Per-unit overlap with an AOI. Areas are geodesic square metres (WGS84 spheroid).
CREATE OR REPLACE FUNCTION geo.unit_overlap(aoi geometry, p_level text DEFAULT NULL)
RETURNS TABLE (unit_id text, name text, level text,
               unit_area_m2 double precision, overlap_area_m2 double precision,
               overlap_fraction double precision)
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    WITH q AS (SELECT cgis.to_4326($1) AS g),
    hits AS (
        SELECT u.unit_id, u.name, u.level,
               ST_Area(u.geom::geography) AS unit_area,
               ST_Area(ST_Intersection(u.geom, q.g)::geography) AS overlap_area
          FROM geo.admin_units u, q
         WHERE u.geom && q.g
           AND ST_Intersects(u.geom, q.g)
           AND ($2 IS NULL OR u.level = $2))
    SELECT unit_id, name, level, unit_area, overlap_area,
           CASE WHEN unit_area > 0 THEN overlap_area / unit_area END
      FROM hits
     WHERE overlap_area > 0
     ORDER BY unit_id
$$;

-- Aggregation of vector features by level, optionally restricted to units
-- intersecting an AOI: count, geodesic area and WGS84 extent.
CREATE OR REPLACE FUNCTION geo.summarize_units(aoi geometry DEFAULT NULL)
RETURNS TABLE (level text, unit_count bigint, total_area_m2 double precision,
               extent geometry)
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    WITH q AS (SELECT CASE WHEN $1 IS NULL THEN NULL ELSE cgis.to_4326($1) END AS g)
    SELECT u.level,
           count(*),
           sum(ST_Area(u.geom::geography)),
           ST_SetSRID(ST_Extent(u.geom)::geometry, 4326)
      FROM geo.admin_units u, q
     WHERE q.g IS NULL OR (u.geom && q.g AND ST_Intersects(u.geom, q.g))
     GROUP BY u.level
     ORDER BY u.level NULLS LAST
$$;
