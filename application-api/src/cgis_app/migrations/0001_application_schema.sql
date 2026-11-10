-- cgis_app 0001: accounts, sessions/tokens, login throttling, dataset access policy,
-- admin uploads and the job queue shared by the API and its workers.
--
-- Owned by application-api/ (Agent 5). It never alters the frozen catalog/geo/ops schemas of
-- database/ (Agent 4); it only READS catalog.datasets / catalog.current_datasets, and the ingest
-- worker writes them through the existing importer with the cgis_ingest privileges.

CREATE SCHEMA IF NOT EXISTS cgis_app;
COMMENT ON SCHEMA cgis_app IS 'Application API: users, sessions, access policy, uploads, jobs (Agent 5).';

-- ------------------------------------------------------------------ accounts
CREATE TABLE IF NOT EXISTS cgis_app.users (
    user_id              uuid         NOT NULL DEFAULT gen_random_uuid(),
    username             text         NOT NULL,
    role                 text         NOT NULL,
    password_hash        text         NOT NULL,
    disabled             boolean      NOT NULL DEFAULT false,
    created_at           timestamptz  NOT NULL DEFAULT now(),
    updated_at           timestamptz  NOT NULL DEFAULT now(),
    password_changed_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT users_pkey PRIMARY KEY (user_id),
    CONSTRAINT users_username_key UNIQUE (username),
    CONSTRAINT users_username_format CHECK (username ~ '^[a-z0-9][a-z0-9._-]{1,62}$'),
    CONSTRAINT users_role_valid CHECK (role IN ('admin', 'user')),
    -- Only argon2id PHC strings are accepted, so a plaintext password can never be stored by mistake.
    CONSTRAINT users_password_hash_format CHECK (password_hash LIKE '$argon2id$%')
);
COMMENT ON TABLE cgis_app.users IS 'Application accounts. Passwords are argon2id hashes only.';

-- Browser sessions, bearer sessions and personal access tokens share one table. Only the SHA-256 of
-- the 256-bit random secret is stored, so a database read does not yield usable credentials.
CREATE TABLE IF NOT EXISTS cgis_app.sessions (
    session_id     uuid         NOT NULL DEFAULT gen_random_uuid(),
    token_hash     bytea        NOT NULL,
    user_id        uuid         NOT NULL,
    kind           text         NOT NULL,
    scope          text         NOT NULL DEFAULT 'full',
    name           text,
    csrf_token     text,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    last_seen_at   timestamptz  NOT NULL DEFAULT now(),
    expires_at     timestamptz  NOT NULL,
    idle_seconds   integer,
    revoked_at     timestamptz,
    revoked_reason text,
    CONSTRAINT sessions_pkey PRIMARY KEY (session_id),
    CONSTRAINT sessions_token_hash_key UNIQUE (token_hash),
    CONSTRAINT sessions_user_fk FOREIGN KEY (user_id) REFERENCES cgis_app.users (user_id) ON DELETE CASCADE,
    CONSTRAINT sessions_kind_valid CHECK (kind IN ('browser', 'bearer', 'pat')),
    CONSTRAINT sessions_scope_valid CHECK (scope IN ('full', 'read', 'admin')),
    CONSTRAINT sessions_scope_kind CHECK ((kind = 'pat') = (scope <> 'full')),
    CONSTRAINT sessions_csrf_browser CHECK ((kind = 'browser') = (csrf_token IS NOT NULL)),
    CONSTRAINT sessions_token_hash_len CHECK (octet_length(token_hash) = 32),
    CONSTRAINT sessions_name_len CHECK (name IS NULL OR length(name) BETWEEN 1 AND 64),
    CONSTRAINT sessions_idle_positive CHECK (idle_seconds IS NULL OR idle_seconds > 0),
    CONSTRAINT sessions_expiry_after_create CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON cgis_app.sessions (user_id, kind) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON cgis_app.sessions (expires_at);

-- Failed-login counters per username ('user:<name>') and per client address ('ip:<addr>').
CREATE TABLE IF NOT EXISTS cgis_app.login_attempts (
    key               text         NOT NULL,
    failures          integer      NOT NULL DEFAULT 0,
    window_started_at timestamptz  NOT NULL DEFAULT now(),
    locked_until      timestamptz,
    CONSTRAINT login_attempts_pkey PRIMARY KEY (key),
    CONSTRAINT login_attempts_key_len CHECK (length(key) BETWEEN 3 AND 200),
    CONSTRAINT login_attempts_failures CHECK (failures >= 0)
);

-- ------------------------------------------------------------------ access policy
-- Datasets without a row use APP_DEFAULT_DATASET_ACCESS (default 'users').
CREATE TABLE IF NOT EXISTS cgis_app.dataset_access (
    dataset_id  text         NOT NULL,
    access      text         NOT NULL,
    note        text,
    updated_by  uuid,
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT dataset_access_pkey PRIMARY KEY (dataset_id),
    CONSTRAINT dataset_access_slug CHECK (dataset_id ~ '^[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$'),
    CONSTRAINT dataset_access_valid CHECK (access IN ('public', 'users', 'admins')),
    CONSTRAINT dataset_access_note_len CHECK (note IS NULL OR length(note) <= 500),
    CONSTRAINT dataset_access_user_fk FOREIGN KEY (updated_by) REFERENCES cgis_app.users (user_id)
        ON DELETE SET NULL
);

-- ------------------------------------------------------------------ admin uploads
-- One row per uploaded file. The file lives at <APP_STAGING_ROOT>/<staged_name>; the name is generated
-- by the server from upload_id and never derived from client input.
CREATE TABLE IF NOT EXISTS cgis_app.uploads (
    upload_id         uuid         NOT NULL DEFAULT gen_random_uuid(),
    dataset_id        text         NOT NULL,
    version           integer      NOT NULL,
    kind              text         NOT NULL,
    metadata          jsonb        NOT NULL,
    original_filename text,
    staged_name       text         NOT NULL,
    size_bytes        bigint       NOT NULL,
    checksum_sha256   text         NOT NULL,
    status            text         NOT NULL DEFAULT 'staged',
    validation        jsonb,
    error             jsonb,
    result            jsonb,
    created_by        uuid,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    published_at      timestamptz,
    CONSTRAINT uploads_pkey PRIMARY KEY (upload_id),
    CONSTRAINT uploads_slug CHECK (dataset_id ~ '^[a-z0-9]([a-z0-9._-]{0,126}[a-z0-9])?$'),
    CONSTRAINT uploads_version_positive CHECK (version > 0),
    CONSTRAINT uploads_kind_valid CHECK (kind IN ('raster', 'vector')),
    CONSTRAINT uploads_metadata_object CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT uploads_staged_name_safe CHECK (staged_name ~ '^[0-9a-f]{32}\.(tif|geojson)$'),
    CONSTRAINT uploads_size_positive CHECK (size_bytes > 0),
    CONSTRAINT uploads_checksum_format CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT uploads_status_valid CHECK (
        status IN ('staged', 'validating', 'validated', 'publishing', 'published', 'failed', 'discarded')),
    CONSTRAINT uploads_original_filename_len CHECK (original_filename IS NULL OR length(original_filename) <= 255),
    CONSTRAINT uploads_user_fk FOREIGN KEY (created_by) REFERENCES cgis_app.users (user_id) ON DELETE SET NULL
);
-- At most one live (not discarded, not published) upload per dataset version.
CREATE UNIQUE INDEX IF NOT EXISTS uploads_live_version_key
    ON cgis_app.uploads (dataset_id, version) WHERE status NOT IN ('discarded', 'published');
CREATE INDEX IF NOT EXISTS uploads_dataset_idx ON cgis_app.uploads (dataset_id, version, created_at DESC);

-- ------------------------------------------------------------------ jobs
-- queue 'ingest': upload validation / validate / publish (ingest worker, assets read-write)
-- queue 'export': reprojection / format conversion / clip (export worker, assets read-only)
CREATE TABLE IF NOT EXISTS cgis_app.jobs (
    job_id            uuid         NOT NULL DEFAULT gen_random_uuid(),
    queue             text         NOT NULL,
    kind              text         NOT NULL,
    status            text         NOT NULL DEFAULT 'queued',
    owner_id          uuid,
    upload_id         uuid,
    dataset_id        text,
    dataset_version   integer,
    input             jsonb        NOT NULL DEFAULT '{}'::jsonb,
    progress          real,
    progress_message  text,
    result            jsonb,
    error             jsonb,
    result_path       text,
    result_size       bigint,
    result_sha256     text,
    attempts          integer      NOT NULL DEFAULT 0,
    max_attempts      integer      NOT NULL DEFAULT 3,
    lease_owner       text,
    lease_expires_at  timestamptz,
    cancel_requested  boolean      NOT NULL DEFAULT false,
    expires_at        timestamptz,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    CONSTRAINT jobs_pkey PRIMARY KEY (job_id),
    CONSTRAINT jobs_queue_valid CHECK (queue IN ('ingest', 'export')),
    CONSTRAINT jobs_kind_valid CHECK (kind IN ('upload', 'validate', 'publish', 'export')),
    CONSTRAINT jobs_kind_queue CHECK ((queue = 'export') = (kind = 'export')),
    CONSTRAINT jobs_status_valid CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')),
    CONSTRAINT jobs_input_object CHECK (jsonb_typeof(input) = 'object'),
    CONSTRAINT jobs_progress_range CHECK (progress IS NULL OR (progress >= 0 AND progress <= 1)),
    CONSTRAINT jobs_error_iff_failed CHECK ((status = 'failed') = (error IS NOT NULL)),
    CONSTRAINT jobs_finished_iff_terminal CHECK (
        (status IN ('completed', 'failed', 'cancelled', 'expired')) = (finished_at IS NOT NULL)),
    CONSTRAINT jobs_result_path_safe CHECK (
        result_path IS NULL OR result_path ~ '^[0-9a-f]{32}/[A-Za-z0-9][A-Za-z0-9._-]{0,200}$'),
    CONSTRAINT jobs_result_sha256 CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT jobs_attempts CHECK (attempts >= 0 AND max_attempts >= 1),
    CONSTRAINT jobs_owner_fk FOREIGN KEY (owner_id) REFERENCES cgis_app.users (user_id) ON DELETE SET NULL,
    CONSTRAINT jobs_upload_fk FOREIGN KEY (upload_id) REFERENCES cgis_app.uploads (upload_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS jobs_claim_idx ON cgis_app.jobs (queue, created_at) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS jobs_owner_idx ON cgis_app.jobs (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_upload_idx ON cgis_app.jobs (upload_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_expiry_idx ON cgis_app.jobs (expires_at) WHERE status = 'completed';

-- Terminal states are final; the transition graph is enforced in the database, not only in Python.
CREATE OR REPLACE FUNCTION cgis_app.jobs_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.updated_at := NEW.created_at;
        IF NEW.status IN ('completed', 'failed', 'cancelled', 'expired') THEN
            NEW.finished_at := coalesce(NEW.finished_at, NEW.created_at);
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.job_id <> OLD.job_id OR NEW.kind <> OLD.kind OR NEW.queue <> OLD.queue OR NEW.input <> OLD.input
       OR NEW.owner_id IS DISTINCT FROM OLD.owner_id AND NEW.owner_id IS NOT NULL THEN
        RAISE EXCEPTION 'job identity, kind, queue, owner and input are immutable (%)', OLD.job_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('failed', 'cancelled', 'expired') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'job % is % (terminal)', OLD.job_id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status = 'completed' AND NEW.status NOT IN ('completed', 'expired') THEN
        RAISE EXCEPTION 'completed job % can only expire', OLD.job_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT (
        NEW.status = OLD.status OR
        (OLD.status = 'queued'  AND NEW.status IN ('running', 'completed', 'failed', 'cancelled')) OR
        (OLD.status = 'running' AND NEW.status IN ('queued', 'completed', 'failed', 'cancelled')) OR
        (OLD.status = 'completed' AND NEW.status = 'expired')
    ) THEN
        RAISE EXCEPTION 'invalid job status transition % -> % for job %', OLD.status, NEW.status, OLD.job_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.created_at := OLD.created_at;
    NEW.updated_at := now();
    IF NEW.status = 'running' AND OLD.status <> 'running' THEN
        NEW.started_at := coalesce(OLD.started_at, now());
    END IF;
    IF NEW.status IN ('completed', 'failed', 'cancelled', 'expired') AND OLD.status <> NEW.status
       AND NOT (OLD.status = 'completed' AND NEW.status = 'expired') THEN
        NEW.finished_at := now();
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER jobs_guard
    BEFORE INSERT OR UPDATE ON cgis_app.jobs
    FOR EACH ROW EXECUTE FUNCTION cgis_app.jobs_guard();

CREATE OR REPLACE FUNCTION cgis_app.touch_updated_at()
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

CREATE OR REPLACE TRIGGER users_touch BEFORE INSERT OR UPDATE ON cgis_app.users
    FOR EACH ROW EXECUTE FUNCTION cgis_app.touch_updated_at();
CREATE OR REPLACE TRIGGER uploads_touch BEFORE INSERT OR UPDATE ON cgis_app.uploads
    FOR EACH ROW EXECUTE FUNCTION cgis_app.touch_updated_at();
