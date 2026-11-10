-- cgis_app 0002: NOLOGIN group roles for the application API and its workers.
-- Login roles (APP_DB_USER, APP_WORKER_DB_USER) are created by `cgis-app migrate` with passwords from
-- the environment and granted one of these groups.
--
--   cgis_app_api     HTTP service: accounts/sessions/policy/uploads/jobs (read-write),
--                    catalog + geo read-only (via cgis_reader). It can NOT write the catalog.
--   cgis_app_worker  ingest/export workers: jobs + uploads (read-write), access policy (read),
--                    catalog/geo writes ONLY through cgis_ingest (the existing importer's role).
--                    It can NOT read users or sessions.

DO $$
DECLARE
    rname text;
BEGIN
    FOREACH rname IN ARRAY ARRAY['cgis_app_api', 'cgis_app_worker'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rname) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', rname);
        END IF;
    END LOOP;
END
$$;

REVOKE ALL ON SCHEMA cgis_app FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA cgis_app FROM PUBLIC;

GRANT USAGE ON SCHEMA cgis_app TO cgis_app_api, cgis_app_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    cgis_app.users, cgis_app.sessions, cgis_app.login_attempts, cgis_app.dataset_access,
    cgis_app.uploads, cgis_app.jobs
TO cgis_app_api;

GRANT SELECT, UPDATE ON cgis_app.jobs, cgis_app.uploads TO cgis_app_worker;
GRANT SELECT ON cgis_app.dataset_access TO cgis_app_worker;

-- Catalog/geo privileges come from the database/ group roles (migration core/0006).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_reader')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_ingest') THEN
        RAISE EXCEPTION 'group roles cgis_reader/cgis_ingest are missing: run the database migrations first '
                        '(python -m cgis_db migrate up)'
            USING ERRCODE = 'undefined_object';
    END IF;
    GRANT cgis_reader TO cgis_app_api;
    GRANT cgis_reader TO cgis_app_worker;
    GRANT cgis_ingest TO cgis_app_worker;
END
$$;
