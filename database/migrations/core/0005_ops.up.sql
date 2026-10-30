-- 0005: job tracking for sync/async processing (contracts/SHARED_CONTRACTS.md § ops).
--
-- Status lifecycle:   queued -> running -> completed | failed
--                     queued -> completed | failed   (synchronous jobs / cancellation)
-- Rows may also be inserted directly as completed/failed for synchronous work.
-- completed and failed are terminal.

CREATE SCHEMA IF NOT EXISTS ops;
COMMENT ON SCHEMA ops IS 'Operational records: processing job tracking.';

CREATE TABLE IF NOT EXISTS ops.jobs (
    job_id       uuid         NOT NULL DEFAULT gen_random_uuid(),
    kind         text         NOT NULL,
    status       text         NOT NULL DEFAULT 'queued',
    input        jsonb        NOT NULL DEFAULT '{}'::jsonb,
    result_path  text,
    result       jsonb,
    error        text,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,

    CONSTRAINT jobs_pkey PRIMARY KEY (job_id),
    CONSTRAINT jobs_kind_format CHECK (kind ~ '^[a-z][a-z0-9_.-]{0,63}$'),
    CONSTRAINT jobs_status_valid CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CONSTRAINT jobs_input_object CHECK (jsonb_typeof(input) = 'object'),
    CONSTRAINT jobs_result_path_safe
        CHECK (result_path IS NULL OR cgis.is_safe_relative_path(result_path)),
    -- error is present iff failed
    CONSTRAINT jobs_error_iff_failed CHECK (
        (status = 'failed') = (error IS NOT NULL AND btrim(error) <> '')),
    -- results only on completed jobs; completed jobs must have one
    CONSTRAINT jobs_result_iff_completed CHECK (
        (status = 'completed') = (result_path IS NOT NULL OR result IS NOT NULL)),
    CONSTRAINT jobs_finished_iff_terminal CHECK (
        (status IN ('completed', 'failed')) = (finished_at IS NOT NULL)),
    CONSTRAINT jobs_started_when_running CHECK (status <> 'running' OR started_at IS NOT NULL),
    CONSTRAINT jobs_time_order CHECK (
        (started_at IS NULL OR started_at >= created_at) AND
        (finished_at IS NULL OR finished_at >= coalesce(started_at, created_at)))
);

COMMENT ON TABLE ops.jobs IS 'Processing jobs. completed => result_path and/or result; failed => error.';
COMMENT ON COLUMN ops.jobs.result_path IS 'Output path RELATIVE to the server export root; never client supplied.';
COMMENT ON COLUMN ops.jobs.result IS 'Inline JSON result (e.g. zonal statistics) for jobs without a file output.';
COMMENT ON COLUMN ops.jobs.error IS 'Safe, user-presentable error message (no stack traces or secrets).';

CREATE INDEX IF NOT EXISTS jobs_status_created_idx ON ops.jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_kind_created_idx ON ops.jobs (kind, created_at DESC);

CREATE OR REPLACE FUNCTION ops.jobs_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.updated_at := NEW.created_at;
        IF NEW.status = 'running' THEN
            NEW.started_at := coalesce(NEW.started_at, NEW.created_at);
        END IF;
        IF NEW.status IN ('completed', 'failed') THEN
            NEW.finished_at := coalesce(NEW.finished_at, NEW.created_at);
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.job_id <> OLD.job_id OR NEW.kind <> OLD.kind OR NEW.input <> OLD.input THEN
        RAISE EXCEPTION 'job_id, kind and input of job % are immutable', OLD.job_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'job % is % (terminal) and cannot be modified', OLD.job_id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT (
        NEW.status = OLD.status OR
        (OLD.status = 'queued'  AND NEW.status IN ('running', 'completed', 'failed')) OR
        (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))
    ) THEN
        RAISE EXCEPTION 'invalid job status transition % -> % for job %', OLD.status, NEW.status, OLD.job_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    NEW.created_at := OLD.created_at;
    NEW.updated_at := now();
    IF NEW.status = 'running' AND OLD.status <> 'running' THEN
        NEW.started_at := now();
    END IF;
    IF NEW.status IN ('completed', 'failed') THEN
        NEW.finished_at := now();
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER jobs_guard
    BEFORE INSERT OR UPDATE ON ops.jobs
    FOR EACH ROW EXECUTE FUNCTION ops.jobs_guard();

CREATE OR REPLACE FUNCTION ops.create_job(p_kind text, p_input jsonb DEFAULT '{}'::jsonb)
RETURNS ops.jobs
LANGUAGE sql
AS $$
    INSERT INTO ops.jobs (kind, input) VALUES ($1, coalesce($2, '{}'::jsonb)) RETURNING *
$$;

CREATE OR REPLACE FUNCTION ops._job_or_raise(p_job_id uuid)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM ops.jobs WHERE job_id = p_job_id) THEN
        RAISE EXCEPTION 'job % not found', p_job_id USING ERRCODE = 'no_data_found';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION ops.start_job(p_job_id uuid)
RETURNS ops.jobs
LANGUAGE plpgsql
AS $$
DECLARE
    r ops.jobs;
BEGIN
    PERFORM ops._job_or_raise(p_job_id);
    UPDATE ops.jobs SET status = 'running' WHERE job_id = p_job_id RETURNING * INTO r;
    RETURN r;
END
$$;

CREATE OR REPLACE FUNCTION ops.complete_job(p_job_id uuid, p_result_path text DEFAULT NULL, p_result jsonb DEFAULT NULL)
RETURNS ops.jobs
LANGUAGE plpgsql
AS $$
DECLARE
    r ops.jobs;
BEGIN
    PERFORM ops._job_or_raise(p_job_id);
    UPDATE ops.jobs SET status = 'completed', result_path = p_result_path, result = p_result
     WHERE job_id = p_job_id RETURNING * INTO r;
    RETURN r;
END
$$;

CREATE OR REPLACE FUNCTION ops.fail_job(p_job_id uuid, p_error text)
RETURNS ops.jobs
LANGUAGE plpgsql
AS $$
DECLARE
    r ops.jobs;
BEGIN
    PERFORM ops._job_or_raise(p_job_id);
    UPDATE ops.jobs SET status = 'failed', error = p_error, result_path = NULL, result = NULL
     WHERE job_id = p_job_id RETURNING * INTO r;
    RETURN r;
END
$$;
