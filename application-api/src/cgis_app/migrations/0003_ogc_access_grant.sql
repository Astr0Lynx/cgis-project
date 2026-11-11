-- cgis_app 0003: let the OGC publisher (role cgis_reader) read the per-dataset access level, so it can
-- withhold 'admins'-only datasets from /ogc/ and /tiles/. Only the two columns it needs are granted.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_reader') THEN
        RAISE EXCEPTION 'group role cgis_reader is missing: run the database migrations first '
                        '(python -m cgis_db migrate up)'
            USING ERRCODE = 'undefined_object';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA cgis_app TO cgis_reader;
GRANT SELECT (dataset_id, access) ON cgis_app.dataset_access TO cgis_reader;
