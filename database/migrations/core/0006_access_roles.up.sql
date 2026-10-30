-- 0006: NOLOGIN group roles describing the consumer read/write surface.
-- Login users (created by infrastructure, with secrets from the environment)
-- are granted one of these, e.g.  GRANT cgis_reader TO ogc_service_user;
--
--   cgis_reader     OGC publication / processing reads: catalog + geo (SELECT)
--   cgis_ingest     ingestion: write catalog.datasets and geo.admin_units
--   cgis_processing processing service: read catalog + geo, read/write ops.jobs
--
-- Roles are cluster-wide. If the migrating user cannot create roles, creation
-- is skipped with a NOTICE and grants are applied only to roles that exist.

DO $$
DECLARE
    can_create boolean;
    rname text;
BEGIN
    SELECT rolsuper OR rolcreaterole INTO can_create FROM pg_roles WHERE rolname = current_user;
    FOREACH rname IN ARRAY ARRAY['cgis_reader', 'cgis_ingest', 'cgis_processing'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rname) THEN
            IF can_create THEN
                EXECUTE format('CREATE ROLE %I NOLOGIN', rname);
            ELSE
                RAISE NOTICE 'skipping CREATE ROLE %: current user lacks CREATEROLE', rname;
            END IF;
        END IF;
    END LOOP;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_reader') THEN
        GRANT USAGE ON SCHEMA cgis, catalog, geo TO cgis_reader;
        GRANT SELECT ON catalog.datasets, catalog.current_datasets, geo.admin_units TO cgis_reader;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cgis TO cgis_reader;
        GRANT EXECUTE ON FUNCTION
            catalog.resolve_dataset(text),
            catalog.datasets_in_bbox(double precision, double precision, double precision, double precision, integer, text),
            catalog.datasets_intersecting(geometry, text),
            geo.units_in_bbox(double precision, double precision, double precision, double precision, integer, text),
            geo.units_intersecting(geometry, text),
            geo.units_covered_by(geometry, text),
            geo.unit_overlap(geometry, text),
            geo.summarize_units(geometry)
        TO cgis_reader;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_ingest') THEN
        GRANT USAGE ON SCHEMA cgis, catalog, geo TO cgis_ingest;
        GRANT SELECT, INSERT, UPDATE ON catalog.datasets, geo.admin_units TO cgis_ingest;
        GRANT SELECT ON catalog.current_datasets TO cgis_ingest;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cgis, catalog, geo TO cgis_ingest;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_processing') THEN
        GRANT USAGE ON SCHEMA cgis, catalog, geo, ops TO cgis_processing;
        GRANT SELECT ON catalog.datasets, catalog.current_datasets, geo.admin_units TO cgis_processing;
        GRANT SELECT, INSERT, UPDATE ON ops.jobs TO cgis_processing;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cgis, ops TO cgis_processing;
        GRANT EXECUTE ON FUNCTION
            catalog.resolve_dataset(text),
            catalog.datasets_in_bbox(double precision, double precision, double precision, double precision, integer, text),
            catalog.datasets_intersecting(geometry, text),
            geo.units_in_bbox(double precision, double precision, double precision, double precision, integer, text),
            geo.units_intersecting(geometry, text),
            geo.units_covered_by(geometry, text),
            geo.unit_overlap(geometry, text),
            geo.summarize_units(geometry)
        TO cgis_processing;
    END IF;
END
$$;
