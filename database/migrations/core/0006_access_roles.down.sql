-- Revokes this database's grants. Roles are cluster-wide and may hold grants in
-- other databases, so each DROP ROLE is attempted and skipped with a NOTICE if
-- the role is still referenced elsewhere.

DO $$
DECLARE
    rname text;
BEGIN
    FOREACH rname IN ARRAY ARRAY['cgis_reader', 'cgis_ingest', 'cgis_processing'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rname) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA catalog, geo, ops FROM %I', rname);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cgis, catalog, geo, ops FROM %I', rname);
            EXECUTE format('REVOKE ALL ON SCHEMA cgis, catalog, geo, ops FROM %I', rname);
            BEGIN
                EXECUTE format('DROP ROLE %I', rname);
            EXCEPTION WHEN dependent_objects_still_exist OR insufficient_privilege THEN
                RAISE NOTICE 'role % kept: %', rname, SQLERRM;
            END;
        END IF;
    END LOOP;
END
$$;
