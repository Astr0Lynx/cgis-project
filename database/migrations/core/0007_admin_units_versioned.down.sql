-- Rollback of 0007. NON-DESTRUCTIVE: it refuses to run while the table holds rows that the old
-- global unit_id primary key cannot represent (several versions or datasets sharing a unit_id).
-- Resolve those deliberately (export, then delete the superseded rows) before rolling back.

DO $$
DECLARE
    dup_count bigint;
    example text;
BEGIN
    SELECT count(*), min(unit_id) INTO dup_count, example
      FROM (SELECT unit_id FROM geo.admin_units GROUP BY unit_id HAVING count(*) > 1) d;
    IF dup_count > 0 THEN
        RAISE EXCEPTION 'cannot roll back 0007: % unit_id value(s) are stored more than once (e.g. %), '
                        'which the pre-0007 global primary key cannot hold', dup_count, example
            USING ERRCODE = 'unique_violation',
                  HINT = 'Keep version history: do not roll back. Otherwise remove superseded rows explicitly first.';
    END IF;
END
$$;

DROP FUNCTION IF EXISTS geo.unit_versions(text, text);
DROP FUNCTION IF EXISTS geo.dataset_units(text, integer);
DROP VIEW IF EXISTS geo.current_admin_units;
DROP TRIGGER IF EXISTS admin_units_guard ON geo.admin_units;
DROP FUNCTION IF EXISTS geo.admin_units_guard();

ALTER TABLE geo.admin_units DROP CONSTRAINT IF EXISTS admin_units_identity;
ALTER TABLE geo.admin_units DROP CONSTRAINT IF EXISTS admin_units_pkey;
ALTER TABLE geo.admin_units ADD CONSTRAINT admin_units_pkey PRIMARY KEY (unit_id);
ALTER TABLE geo.admin_units DROP COLUMN IF EXISTS unit_key;
DROP INDEX IF EXISTS geo.admin_units_unit_id_idx;
CREATE INDEX IF NOT EXISTS admin_units_dataset_idx ON geo.admin_units (dataset_id, dataset_version);

COMMENT ON TABLE geo.admin_units IS
    'Analysis units. geom is a valid 2D MultiPolygon in EPSG:4326; write through geo.upsert_admin_unit to normalize.';

-- Restore the 0004 definitions.
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
