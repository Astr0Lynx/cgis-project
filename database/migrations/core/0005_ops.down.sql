DROP FUNCTION IF EXISTS ops.fail_job(uuid, text);
DROP FUNCTION IF EXISTS ops.complete_job(uuid, text, jsonb);
DROP FUNCTION IF EXISTS ops.start_job(uuid);
DROP FUNCTION IF EXISTS ops._job_or_raise(uuid);
DROP FUNCTION IF EXISTS ops.create_job(text, jsonb);
DROP TABLE IF EXISTS ops.jobs;
DROP FUNCTION IF EXISTS ops.jobs_guard();
DROP SCHEMA IF EXISTS ops;
