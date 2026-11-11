"""PostgreSQL job queue shared by the API (enqueue/inspect/cancel) and the workers (claim/run).

* Claiming uses ``FOR UPDATE SKIP LOCKED`` so several workers never take the same job.
* A claimed job holds a lease that the worker extends while it reports progress. If a worker dies,
  the lease expires and the job is re-queued (``attempts`` counts tries); after ``max_attempts`` it fails
  with ``JOB_ABANDONED``. Work is written to temporary files and published atomically, so a retried job
  never sees a half-written output.
* Status graph (enforced by a trigger): queued -> running -> completed | failed | cancelled;
  running -> queued (lease expiry); completed -> expired (output retention elapsed).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

ACTIVE = ("queued", "running")
TERMINAL = ("completed", "failed", "cancelled", "expired")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(conn: psycopg.Connection, *, queue: str, kind: str, owner_id: uuid.UUID | None, input: dict[str, Any],
            upload_id: uuid.UUID | None = None, dataset_id: str | None = None, dataset_version: int | None = None,
            max_attempts: int = 3, progress_message: str | None = "queued", job_id: uuid.UUID | None = None) -> dict:
    return conn.execute(
        "INSERT INTO cgis_app.jobs (job_id, queue, kind, owner_id, upload_id, dataset_id, dataset_version, input, "
        "max_attempts, progress_message) VALUES (coalesce(%s, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING *",
        (job_id, queue, kind, owner_id, upload_id, dataset_id, dataset_version, Jsonb(input), max_attempts,
         progress_message)).fetchone()


def insert_completed(conn: psycopg.Connection, *, queue: str, kind: str, owner_id: uuid.UUID | None,
                     input: dict[str, Any], result: dict[str, Any], dataset_id: str | None = None,
                     dataset_version: int | None = None, upload_id: uuid.UUID | None = None,
                     result_size: int | None = None, result_sha256: str | None = None,
                     expires_at: datetime | None = None) -> dict:
    return conn.execute(
        "INSERT INTO cgis_app.jobs (queue, kind, status, owner_id, upload_id, dataset_id, dataset_version, input, "
        "result, result_size, result_sha256, progress, progress_message, expires_at, started_at) "
        "VALUES (%s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, %s, 1, 'completed', %s, now()) RETURNING *",
        (queue, kind, owner_id, upload_id, dataset_id, dataset_version, Jsonb(input), Jsonb(result), result_size,
         result_sha256, expires_at)).fetchone()


def get(conn: psycopg.Connection, job_id: uuid.UUID) -> dict | None:
    return conn.execute("SELECT * FROM cgis_app.jobs WHERE job_id = %s", (job_id,)).fetchone()


def list_jobs(conn: psycopg.Connection, *, queue: str | None = None, owner_id: uuid.UUID | None = None,
              status: str | None = None, kind: str | None = None, limit: int = 50, offset: int = 0
              ) -> tuple[list[dict], int]:
    where, params = [], {}
    for col, val in (("queue", queue), ("owner_id", owner_id), ("status", status), ("kind", kind)):
        if val is not None:
            where.append(f"{col} = %({col})s")
            params[col] = val
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT count(*) AS n FROM cgis_app.jobs {clause}", params).fetchone()["n"]
    rows = conn.execute(f"SELECT * FROM cgis_app.jobs {clause} ORDER BY created_at DESC LIMIT %(limit)s "
                        "OFFSET %(offset)s", {**params, "limit": limit, "offset": offset}).fetchall()
    return rows, total


def count_active(conn: psycopg.Connection, *, owner_id: uuid.UUID, queue: str) -> int:
    return conn.execute("SELECT count(*) AS n FROM cgis_app.jobs WHERE owner_id = %s AND queue = %s "
                        "AND status IN ('queued', 'running')", (owner_id, queue)).fetchone()["n"]


def claim(conn: psycopg.Connection, queue: str, worker_id: str, lease_seconds: int) -> dict | None:
    """Take the oldest runnable job (queued, or running with an expired lease)."""
    with conn.transaction():
        # Jobs whose lease expired too many times are failed instead of retried forever.
        conn.execute(
            "UPDATE cgis_app.jobs SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL, "
            "error = jsonb_build_object('code', 'JOB_ABANDONED', 'message', "
            "'the job was interrupted ' || attempts || ' time(s) (worker restart or crash) and was not retried') "
            "WHERE queue = %s AND status = 'running' AND lease_expires_at < now() AND attempts >= max_attempts",
            (queue,))
        row = conn.execute(
            """
            UPDATE cgis_app.jobs j SET status = 'running', lease_owner = %(worker)s,
                   lease_expires_at = now() + make_interval(secs => %(lease)s), attempts = j.attempts + 1,
                   progress_message = CASE WHEN j.status = 'running' THEN 'resumed after interruption'
                                           ELSE 'started' END
             WHERE j.job_id = (
                   SELECT job_id FROM cgis_app.jobs
                    WHERE queue = %(queue)s AND NOT cancel_requested
                      AND (status = 'queued' OR (status = 'running' AND lease_expires_at < now()))
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1)
            RETURNING j.*
            """, {"worker": worker_id, "lease": lease_seconds, "queue": queue}).fetchone()
    return row


def heartbeat(conn: psycopg.Connection, job_id: uuid.UUID, worker_id: str, *, lease_seconds: int,
              progress: float | None = None, message: str | None = None) -> bool:
    """Extend the lease and record progress; returns True when cancellation was requested.

    Raises LookupError if the job is no longer leased by this worker (e.g. lease lost).
    """
    row = conn.execute(
        "UPDATE cgis_app.jobs SET lease_expires_at = now() + make_interval(secs => %s), "
        "progress = coalesce(%s, progress), progress_message = coalesce(%s, progress_message) "
        "WHERE job_id = %s AND lease_owner = %s AND status = 'running' RETURNING cancel_requested",
        (lease_seconds, None if progress is None else max(0.0, min(1.0, float(progress))), message, job_id,
         worker_id)).fetchone()
    if row is None:
        raise LookupError(f"job {job_id} is no longer leased by {worker_id}")
    return bool(row["cancel_requested"])


def complete(conn: psycopg.Connection, job_id: uuid.UUID, worker_id: str, *, result: dict[str, Any],
             result_path: str | None = None, result_size: int | None = None, result_sha256: str | None = None,
             expires_at: datetime | None = None) -> dict | None:
    return conn.execute(
        "UPDATE cgis_app.jobs SET status = 'completed', result = %s, result_path = %s, result_size = %s, "
        "result_sha256 = %s, expires_at = %s, progress = 1, progress_message = 'completed', lease_owner = NULL, "
        "lease_expires_at = NULL WHERE job_id = %s AND lease_owner = %s AND status = 'running' RETURNING *",
        (Jsonb(result), result_path, result_size, result_sha256, expires_at, job_id, worker_id)).fetchone()


def fail(conn: psycopg.Connection, job_id: uuid.UUID, worker_id: str | None, error: dict[str, Any]) -> dict | None:
    params: tuple = (Jsonb(error), job_id)
    owner_clause = ""
    if worker_id:
        owner_clause = "AND lease_owner = %s"
        params += (worker_id,)
    return conn.execute(
        f"UPDATE cgis_app.jobs SET status = 'failed', error = %s, progress_message = 'failed', lease_owner = NULL, "
        f"lease_expires_at = NULL WHERE job_id = %s {owner_clause} AND status IN ('queued', 'running') RETURNING *",
        params).fetchone()


def mark_cancelled(conn: psycopg.Connection, job_id: uuid.UUID, worker_id: str) -> dict | None:
    return conn.execute(
        "UPDATE cgis_app.jobs SET status = 'cancelled', progress_message = 'cancelled', lease_owner = NULL, "
        "lease_expires_at = NULL WHERE job_id = %s AND lease_owner = %s AND status = 'running' RETURNING *",
        (job_id, worker_id)).fetchone()


def request_cancel(conn: psycopg.Connection, job_id: uuid.UUID) -> dict | None:
    """Queued -> cancelled immediately; running -> flag for the worker. Returns the updated row."""
    with conn.transaction():
        row = conn.execute("SELECT * FROM cgis_app.jobs WHERE job_id = %s FOR UPDATE", (job_id,)).fetchone()
        if row is None or row["status"] not in ACTIVE:
            return row
        if row["status"] == "queued":
            return conn.execute("UPDATE cgis_app.jobs SET status = 'cancelled', cancel_requested = true, "
                                "progress_message = 'cancelled' WHERE job_id = %s RETURNING *", (job_id,)).fetchone()
        return conn.execute("UPDATE cgis_app.jobs SET cancel_requested = true, progress_message = 'cancelling' "
                            "WHERE job_id = %s RETURNING *", (job_id,)).fetchone()


def expire_due(conn: psycopg.Connection, queue: str, *, limit: int = 200) -> list[dict]:
    """Completed jobs whose retention elapsed become ``expired``; returns them so outputs can be deleted."""
    return conn.execute(
        "UPDATE cgis_app.jobs SET status = 'expired', progress_message = 'output expired' "
        "WHERE job_id IN (SELECT job_id FROM cgis_app.jobs WHERE queue = %s AND status = 'completed' "
        "AND expires_at IS NOT NULL AND expires_at <= now() ORDER BY expires_at LIMIT %s) RETURNING *",
        (queue, limit)).fetchall()


def live_result_paths(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT result_path FROM cgis_app.jobs WHERE result_path IS NOT NULL "
                        "AND status IN ('queued', 'running', 'completed')").fetchall()
    return {r["result_path"] for r in rows}


def total_result_bytes(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT coalesce(sum(result_size), 0) AS n FROM cgis_app.jobs WHERE status = 'completed' "
                       "AND result_path IS NOT NULL").fetchone()
    return int(row["n"])


def expires_in(seconds: int) -> datetime:
    return _now() + timedelta(seconds=seconds)
