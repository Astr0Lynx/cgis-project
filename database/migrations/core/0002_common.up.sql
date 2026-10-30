-- 0002: shared helper functions used by the catalog, geo and ops schemas.

CREATE SCHEMA IF NOT EXISTS cgis;
COMMENT ON SCHEMA cgis IS 'CyberGIS shared helper functions (no tables).';

-- A server-side asset/result path must be RELATIVE to a server-owned root
-- (e.g. /data/assets). Rejects absolute paths, drive letters, URL schemes,
-- backslashes, control characters, empty/./.. segments and trailing slashes.
CREATE OR REPLACE FUNCTION cgis.is_safe_relative_path(p text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT length(p) BETWEEN 1 AND 1024
       AND p !~ '^/'
       AND p !~ '/$'
       AND p !~ '\\'
       AND p !~ '[[:cntrl:]]'
       AND p !~ '^[A-Za-z][A-Za-z0-9+.-]*:'
       AND p !~ '//'
       AND p !~ '(^|/)\.{1,2}(/|$)'
$$;

-- 'EPSG:<code>' -> SRID present in spatial_ref_sys, or a clear error.
CREATE OR REPLACE FUNCTION cgis.srid_from_crs(p_crs text)
RETURNS integer
LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE
AS $$
DECLARE
    v_srid integer;
BEGIN
    IF p_crs !~ '^EPSG:[1-9][0-9]{0,5}$' THEN
        RAISE EXCEPTION 'invalid CRS "%": expected EPSG:<code>', p_crs
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT srid INTO v_srid
      FROM public.spatial_ref_sys
     WHERE auth_name = 'EPSG' AND auth_srid = substr(p_crs, 6)::integer;
    IF v_srid IS NULL THEN
        RAISE EXCEPTION 'unknown CRS "%": not present in spatial_ref_sys', p_crs
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_srid;
END
$$;

-- Any geometry with an explicit, known SRID -> the same geometry in EPSG:4326.
-- SRID 0 (unknown) is rejected rather than silently assumed to be 4326.
CREATE OR REPLACE FUNCTION cgis.to_4326(g geometry)
RETURNS geometry
LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE
AS $$
BEGIN
    IF ST_SRID(g) = 0 THEN
        RAISE EXCEPTION 'geometry has no SRID; declare its CRS explicitly'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.spatial_ref_sys WHERE srid = ST_SRID(g)) THEN
        RAISE EXCEPTION 'geometry SRID % is not present in spatial_ref_sys', ST_SRID(g)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF ST_SRID(g) = 4326 THEN
        -- Projected coordinates mislabelled as EPSG:4326 are a common error.
        IF NOT ST_IsEmpty(g) AND (ST_XMin(g) < -180 OR ST_XMax(g) > 180
                                  OR ST_YMin(g) < -90 OR ST_YMax(g) > 90) THEN
            RAISE EXCEPTION 'coordinates outside EPSG:4326 range [-180,-90,180,90]; is the CRS declared correctly?'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN g;
    END IF;
    RETURN ST_Transform(g, 4326);
END
$$;

-- Query envelope in any CRS -> conservative EPSG:4326 Polygon. Edges are
-- densified before reprojection so curved edges are not cut off.
CREATE OR REPLACE FUNCTION cgis.envelope_4326(
    xmin double precision, ymin double precision,
    xmax double precision, ymax double precision,
    srid integer DEFAULT 4326)
RETURNS geometry
LANGUAGE plpgsql STABLE PARALLEL SAFE
AS $$
DECLARE
    env geometry;
BEGIN
    IF xmin IS NULL OR ymin IS NULL OR xmax IS NULL OR ymax IS NULL OR srid IS NULL THEN
        RAISE EXCEPTION 'bbox requires xmin, ymin, xmax, ymax and srid'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT (xmin < xmax AND ymin < ymax) THEN
        RAISE EXCEPTION 'invalid bbox (%, %, %, %): require xmin < xmax and ymin < ymax',
            xmin, ymin, xmax, ymax USING ERRCODE = 'invalid_parameter_value';
    END IF;
    env := ST_MakeEnvelope(xmin, ymin, xmax, ymax, srid);
    IF srid = 4326 THEN
        IF xmin < -180 OR xmax > 180 OR ymin < -90 OR ymax > 90 THEN
            RAISE EXCEPTION 'EPSG:4326 bbox outside [-180,-90,180,90]'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN env;
    END IF;
    env := ST_Segmentize(env, greatest(xmax - xmin, ymax - ymin) / 64.0);
    RETURN ST_Envelope(cgis.to_4326(env));
END
$$;

CREATE OR REPLACE FUNCTION cgis.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        NEW.created_at := OLD.created_at;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;
