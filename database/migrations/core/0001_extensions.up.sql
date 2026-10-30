-- 0001: PostGIS extension and migration-independent prerequisites.
-- PostGIS Raster is deliberately NOT enabled here; see migrations/raster_demo/.

DO $$
BEGIN
    IF current_setting('server_version_num')::int < 140000 THEN
        RAISE EXCEPTION 'CyberGIS database requires PostgreSQL >= 14 (found %)',
            current_setting('server_version');
    END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS postgis;

DO $$
DECLARE
    v int[] := string_to_array(split_part(postgis_lib_version(), ' ', 1), '.')::int[];
BEGIN
    IF v[1] < 3 OR (v[1] = 3 AND v[2] < 1) THEN
        RAISE EXCEPTION 'CyberGIS database requires PostGIS >= 3.1 (found %)', postgis_lib_version();
    END IF;
END
$$;
