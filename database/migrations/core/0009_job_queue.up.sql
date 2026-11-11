-- 0009: bounded background-job support on ops.jobs (completion P3: large/slow jobs need safe
-- timeouts, status, retries and cancellation). Local PostGIS queue; no Redis or cloud service.
--
-- Additive: every new column has a default, so existing rows and the synchronous processing path
-- (execution = 'sync', max_attempts = 1) behave exactly as before.
--
--   execution         'sync' (run inside the HTTP request) | 'async' (claimed by a worker)
--   attempts          times a worker started the job; max_attempts bounds automatic retries
--   progress          0..1 while running (1 when completed); progress_message is user-presentable
--   heartbeat_at      refreshed by the running worker; a stale heartbeat means the worker died
--   worker_id         opaque id of the claiming worker (host/pid), for operators
--   cancel_requested  set by the API; a queued job fails at once, a running one at its next checkpoint
--
-- New transition: running -> queued, for ASYNC jobs only (a stale job re-queued for retry while
-- attempts < max_attempts). Synchronous jobs keep the 0005 rules.
-- Claiming uses SELECT ... FOR UPDATE SKIP LOCKED, so several workers never take the same job.

ALTER TABLE ops.jobs
    ADD COLUMN IF NOT EXISTS execution        text             NOT NULL DEFAULT 'sync',
    ADD COLUMN IF NOT EXISTS attempts         integer          NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_attempts     integer          NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS progress         double precision,
    ADD COLUMN IF NOT EXISTS progress_message text,
    ADD COLUMN IF NOT EXISTS heartbeat_at     timestamptz,
    ADD COLUMN IF NOT EXISTS worker_id        text,
    ADD COLUMN IF NOT EXISTS cancel_requested boolean          NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'ops.jobs'::regclass
                      AND conname = 'jobs_execution_valid') THEN
        ALTER TABLE ops.jobs ADD CONSTRAINT jobs_execution_valid CHECK (execution IN ('sync', 'async'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'ops.jobs'::regclass
                      AND conname = 'jobs_attempts_valid') THEN
        ALTER TABLE ops.jobs ADD CONSTRAINT jobs_attempts_valid
            CHECK (attempts >= 0 AND max_attempts BETWEEN 1 AND 10);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'ops.jobs'::regclass
                      AND conname = 'jobs_progress_range') THEN
        ALTER TABLE ops.jobs ADD CONSTRAINT jobs_progress_range
            CHECK (progress IS NULL OR (progress >= 0 AND progress <= 1));
    END IF;
END
$$;

COMMENT ON COLUMN ops.jobs.execution IS 'sync: ran inside the HTTP request; async: queued for a background worker.';
COMMENT ON COLUMN ops.jobs.heartbeat_at IS 'Refreshed by the running worker; stale => worker lost, job re-queued or failed.';
COMMENT ON COLUMN ops.jobs.cancel_requested IS 'Cancellation request; the job ends as failed with error code CANCELLED.';

CREATE INDEX IF NOT EXISTS jobs_async_queue_idx ON ops.jobs (created_at)
    WHERE status = 'queued' AND execution = 'async';
CREATE INDEX IF NOT EXISTS jobs_async_running_idx ON ops.jobs (heartbeat_at)
    WHERE status = 'running' AND execution = 'async';

-- 0005's guard plus: running -> queued (retry), and the new columns stay mutable while the job is
-- not terminal. Terminal jobs remain immutable.
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

    IF NEW.job_id <> OLD.job_id OR NEW.kind <> OLD.kind OR NEW.input <> OLD.input
       OR NEW.execution <> OLD.execution THEN
        RAISE EXCEPTION 'job_id, kind, input and execution of job % are immutable', OLD.job_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'job % is % (terminal) and cannot be modified', OLD.job_id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT (
        NEW.status = OLD.status OR
        (OLD.status = 'queued'  AND NEW.status IN ('running', 'completed', 'failed')) OR
        (OLD.status = 'running' AND NEW.status IN ('completed', 'failed')) OR
        (OLD.status = 'running' AND NEW.status = 'queued' AND OLD.execution = 'async')
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

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_processing') THEN
        GRANT SELECT, INSERT, UPDATE ON ops.jobs TO cgis_processing;
    END IF;
END
$$;
