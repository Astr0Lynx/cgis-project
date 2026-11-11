-- Rollback of 0009. Jobs that are queued/running for a background worker cannot be represented by
-- the 0005 model; refuse instead of silently dropping them.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM ops.jobs WHERE execution = 'async' AND status IN ('queued', 'running')) THEN
        RAISE EXCEPTION 'cannot roll back 0009 while asynchronous jobs are queued or running'
            USING ERRCODE = 'object_in_use',
                  HINT = 'Stop the processing workers and wait for (or cancel) active jobs first.';
    END IF;
END
$$;

-- Restore the 0005 guard.
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

DROP INDEX IF EXISTS ops.jobs_async_running_idx;
DROP INDEX IF EXISTS ops.jobs_async_queue_idx;
ALTER TABLE ops.jobs
    DROP CONSTRAINT IF EXISTS jobs_progress_range,
    DROP CONSTRAINT IF EXISTS jobs_attempts_valid,
    DROP CONSTRAINT IF EXISTS jobs_execution_valid,
    DROP COLUMN IF EXISTS cancel_requested,
    DROP COLUMN IF EXISTS worker_id,
    DROP COLUMN IF EXISTS heartbeat_at,
    DROP COLUMN IF EXISTS progress_message,
    DROP COLUMN IF EXISTS progress,
    DROP COLUMN IF EXISTS max_attempts,
    DROP COLUMN IF EXISTS attempts,
    DROP COLUMN IF EXISTS execution;
