"""Job records (``ops.jobs`` in PostGIS, or in memory for tests/dev).

Status values and allowed transitions::

    queued -> running -> completed
    queued -> failed, running -> failed
    running -> queued            (async jobs only: a lost worker's job is retried)

``result_path`` is relative to the output root (never an absolute server path) and
``error`` holds a JSON-encoded ``{"code", "message"}`` object.

Synchronous jobs (``execution = 'sync'``) run inside the HTTP request exactly as in Phase 2.
Asynchronous jobs (``execution = 'async'``, database migration core/0009) are claimed by a
background worker with ``FOR UPDATE SKIP LOCKED``; the worker refreshes ``heartbeat_at`` and
``progress`` while running, and honours ``cancel_requested`` at its next checkpoint.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

QUEUED, RUNNING, COMPLETED, FAILED = "queued", "running", "completed", "failed"
STATUSES = (QUEUED, RUNNING, COMPLETED, FAILED)
SYNC, ASYNC = "sync", "async"
_ALLOWED = {
    QUEUED: {RUNNING, FAILED},
    RUNNING: {COMPLETED, FAILED},
    COMPLETED: set(),
    FAILED: set(),
}


class InvalidTransition(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Job:
    job_id: uuid.UUID
    kind: str
    status: str
    input: dict[str, Any]
    result_path: str | None = None
    error: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    execution: str = SYNC
    attempts: int = 0
    max_attempts: int = 1
    progress: float | None = None
    progress_message: str | None = None
    cancel_requested: bool = False
    heartbeat_at: datetime | None = None
    worker_id: str | None = None


def _check_transition(old: str, new: str, execution: str = SYNC) -> None:
    if old == RUNNING and new == QUEUED and execution == ASYNC:
        return
    if new not in _ALLOWED.get(old, set()):
        raise InvalidTransition(f"job status cannot change from {old} to {new}")


def encode_error(error: dict[str, Any] | None) -> str | None:
    return json.dumps(error, sort_keys=True) if error else None


def decode_error(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    return {"code": "ERROR", "message": text}


class JobStore(Protocol):
    supports_async: bool

    def create(self, kind: str, input: dict[str, Any], *, execution: str = SYNC,
               max_attempts: int = 1) -> Job: ...

    def transition(
        self,
        job_id: uuid.UUID,
        status: str,
        *,
        result_path: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> Job: ...

    def get(self, job_id: uuid.UUID) -> Job | None: ...

    def ping(self) -> None: ...

    # -- async queue (only when supports_async) --
    def claim(self, worker_id: str, kinds: tuple[str, ...]) -> Job | None: ...

    def heartbeat(self, job_id: uuid.UUID, progress: float | None, message: str | None) -> bool: ...

    def request_cancel(self, job_id: uuid.UUID) -> Job | None: ...

    def recover_stale(self, stale_seconds: float) -> dict[str, int]: ...

    def expire_queued(self, ttl_seconds: float) -> int: ...


class InMemoryJobStore:
    """Thread-safe store; keeps a transition history for tests."""

    supports_async = True

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, Job] = {}
        self._lock = threading.Lock()
        self.history: dict[uuid.UUID, list[str]] = {}

    def create(self, kind: str, input: dict[str, Any], *, execution: str = SYNC, max_attempts: int = 1) -> Job:
        job = Job(job_id=uuid.uuid4(), kind=kind, status=QUEUED, input=input, execution=execution,
                  max_attempts=max_attempts)
        with self._lock:
            self._jobs[job.job_id] = job
            self.history[job.job_id] = [QUEUED]
        return job

    def transition(self, job_id, status, *, result_path=None, error=None) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            _check_transition(job.status, status, job.execution)
            job = replace(
                job,
                status=status,
                result_path=result_path if result_path is not None else job.result_path,
                error=error if error is not None else job.error,
                progress=1.0 if status == COMPLETED else job.progress,
                updated_at=_utcnow(),
            )
            self._jobs[job_id] = job
            self.history[job_id].append(status)
            return job

    def get(self, job_id: uuid.UUID) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def ping(self) -> None:
        return None

    def claim(self, worker_id: str, kinds: tuple[str, ...]) -> Job | None:
        with self._lock:
            queued = sorted((j for j in self._jobs.values()
                             if j.status == QUEUED and j.execution == ASYNC and j.kind in kinds
                             and not j.cancel_requested), key=lambda j: j.created_at)
            if not queued:
                return None
            job = replace(queued[0], status=RUNNING, attempts=queued[0].attempts + 1, worker_id=worker_id,
                          heartbeat_at=_utcnow(), updated_at=_utcnow())
            self._jobs[job.job_id] = job
            self.history[job.job_id].append(RUNNING)
            return job

    def heartbeat(self, job_id, progress, message) -> bool:
        with self._lock:
            job = self._jobs[job_id]
            if job.status != RUNNING:
                return job.cancel_requested
            self._jobs[job_id] = replace(job, progress=progress if progress is not None else job.progress,
                                         progress_message=message or job.progress_message,
                                         heartbeat_at=_utcnow(), updated_at=_utcnow())
            return job.cancel_requested

    def request_cancel(self, job_id) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in (COMPLETED, FAILED):
                return job
            job = replace(job, cancel_requested=True, updated_at=_utcnow())
            self._jobs[job_id] = job
            return job

    def recover_stale(self, stale_seconds: float) -> dict[str, int]:
        cutoff = _utcnow() - timedelta(seconds=stale_seconds)
        out = {"requeued": 0, "failed": 0}
        with self._lock:
            for job in list(self._jobs.values()):
                if job.status != RUNNING or job.execution != ASYNC:
                    continue
                if job.heartbeat_at is not None and job.heartbeat_at >= cutoff:
                    continue
                if job.attempts < job.max_attempts and not job.cancel_requested:
                    self._jobs[job.job_id] = replace(job, status=QUEUED, worker_id=None, updated_at=_utcnow())
                    self.history[job.job_id].append(QUEUED)
                    out["requeued"] += 1
                else:
                    self._jobs[job.job_id] = replace(job, status=FAILED, error=WORKER_LOST_ERROR, updated_at=_utcnow())
                    self.history[job.job_id].append(FAILED)
                    out["failed"] += 1
        return out

    def expire_queued(self, ttl_seconds: float) -> int:
        cutoff = _utcnow() - timedelta(seconds=ttl_seconds)
        n = 0
        with self._lock:
            for job in list(self._jobs.values()):
                if job.status == QUEUED and job.execution == ASYNC and job.created_at < cutoff:
                    self._jobs[job.job_id] = replace(job, status=FAILED, error=EXPIRED_ERROR, updated_at=_utcnow())
                    self.history[job.job_id].append(FAILED)
                    n += 1
        return n

    # test helper
    def age(self, job_id: uuid.UUID, *, created_seconds: float = 0, heartbeat_seconds: float | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            changes: dict[str, Any] = {"created_at": job.created_at - timedelta(seconds=created_seconds)}
            if heartbeat_seconds is not None:
                changes["heartbeat_at"] = _utcnow() - timedelta(seconds=heartbeat_seconds)
            self._jobs[job_id] = replace(job, **changes)


WORKER_LOST_ERROR = {"code": "WORKER_LOST",
                     "message": "the worker running this job stopped (restart or crash) and retries are exhausted"}
EXPIRED_ERROR = {"code": "JOB_EXPIRED", "message": "the job waited in the queue longer than the queue TTL"}
CANCELLED_ERROR = {"code": "CANCELLED", "message": "the job was cancelled at the client's request"}

_SELECT = ("job_id, kind, status, input, result_path, error, created_at, updated_at")
_SELECT_ASYNC = (_SELECT + ", execution, attempts, max_attempts, progress, progress_message, "
                 "cancel_requested, heartbeat_at, worker_id")


class PostgresJobStore:
    """``ops.jobs`` in PostGIS. Async support needs the columns of database migration core/0009."""

    def __init__(self, conninfo: str | None):
        self._conninfo = conninfo or ""
        self._async: bool | None = None

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._conninfo, row_factory=dict_row, connect_timeout=5)

    @property
    def supports_async(self) -> bool:
        if self._async is None:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT count(*) AS n FROM information_schema.columns WHERE table_schema = 'ops' "
                        "AND table_name = 'jobs' AND column_name IN ('execution', 'heartbeat_at', "
                        "'cancel_requested', 'attempts')").fetchone()
                self._async = row["n"] == 4
            except Exception:
                return False
        return self._async

    @property
    def _cols(self) -> str:
        return _SELECT_ASYNC if self.supports_async else _SELECT

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> Job:
        payload = row["input"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        extra = {}
        if "execution" in row:
            extra = {k: row[k] for k in ("execution", "attempts", "max_attempts", "progress", "progress_message",
                                         "cancel_requested", "heartbeat_at", "worker_id")}
        return Job(
            job_id=row["job_id"],
            kind=row["kind"],
            status=row["status"],
            input=payload or {},
            result_path=row["result_path"],
            error=decode_error(row["error"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            **extra,
        )

    def create(self, kind: str, input: dict[str, Any], *, execution: str = SYNC, max_attempts: int = 1) -> Job:
        from psycopg.types.json import Jsonb

        job_id = uuid.uuid4()
        with self._connect() as conn:
            if execution == ASYNC or (self.supports_async and max_attempts != 1):
                if not self.supports_async:
                    raise RuntimeError("ops.jobs has no async columns; apply database migration core/0009")
                row = conn.execute(
                    "INSERT INTO ops.jobs (job_id, kind, status, input, execution, max_attempts, created_at, "
                    f"updated_at) VALUES (%s, %s, %s, %s, %s, %s, now(), now()) RETURNING {self._cols}",
                    (job_id, kind, QUEUED, Jsonb(input), execution, max_attempts),
                ).fetchone()
            else:
                row = conn.execute(
                    "INSERT INTO ops.jobs (job_id, kind, status, input, created_at, updated_at) "
                    f"VALUES (%s, %s, %s, %s, now(), now()) RETURNING {self._cols}",
                    (job_id, kind, QUEUED, Jsonb(input)),
                ).fetchone()
        return self._row_to_job(row)

    def transition(self, job_id, status, *, result_path=None, error=None) -> Job:
        with self._connect() as conn:
            with conn.transaction():
                current = conn.execute(
                    f"SELECT {self._cols} FROM ops.jobs WHERE job_id = %s FOR UPDATE", (job_id,)
                ).fetchone()
                if current is None:
                    raise KeyError(str(job_id))
                _check_transition(current["status"], status, current.get("execution", SYNC))
                progress_sql = ", progress = 1" if (status == COMPLETED and self.supports_async) else ""
                row = conn.execute(
                    "UPDATE ops.jobs SET status = %s, result_path = COALESCE(%s, result_path), "
                    f"error = COALESCE(%s, error), updated_at = now(){progress_sql} WHERE job_id = %s "
                    f"RETURNING {self._cols}",
                    (status, result_path, encode_error(error), job_id),
                ).fetchone()
        return self._row_to_job(row)

    def get(self, job_id: uuid.UUID) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {self._cols} FROM ops.jobs WHERE job_id = %s", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1 FROM ops.jobs LIMIT 0")

    # ------------------------------------------------------------------ async queue
    def claim(self, worker_id: str, kinds: tuple[str, ...]) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "UPDATE ops.jobs SET status = 'running', attempts = attempts + 1, worker_id = %s, "
                "heartbeat_at = now(), progress = 0 "
                "WHERE job_id = (SELECT job_id FROM ops.jobs WHERE status = 'queued' AND execution = 'async' "
                "AND kind = ANY(%s) AND NOT cancel_requested ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1) "
                f"RETURNING {_SELECT_ASYNC}",
                (worker_id, list(kinds)),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def heartbeat(self, job_id, progress, message) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "UPDATE ops.jobs SET heartbeat_at = now(), progress = COALESCE(%s, progress), "
                "progress_message = COALESCE(%s, progress_message) "
                "WHERE job_id = %s AND status = 'running' RETURNING cancel_requested",
                (progress, message, job_id),
            ).fetchone()
            if row is None:
                row = conn.execute("SELECT cancel_requested FROM ops.jobs WHERE job_id = %s", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def request_cancel(self, job_id) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "UPDATE ops.jobs SET cancel_requested = true WHERE job_id = %s AND status IN ('queued', 'running') "
                f"RETURNING {_SELECT_ASYNC}", (job_id,)).fetchone()
            if row is None:
                row = conn.execute(f"SELECT {_SELECT_ASYNC} FROM ops.jobs WHERE job_id = %s", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def recover_stale(self, stale_seconds: float) -> dict[str, int]:
        with self._connect() as conn:
            with conn.transaction():
                stale = conn.execute(
                    "SELECT job_id, attempts, max_attempts, cancel_requested FROM ops.jobs "
                    "WHERE status = 'running' AND execution = 'async' "
                    "AND (heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => %s)) "
                    "FOR UPDATE SKIP LOCKED", (stale_seconds,)).fetchall()
                out = {"requeued": 0, "failed": 0}
                for r in stale:
                    if r["attempts"] < r["max_attempts"] and not r["cancel_requested"]:
                        conn.execute("UPDATE ops.jobs SET status = 'queued', worker_id = NULL WHERE job_id = %s",
                                     (r["job_id"],))
                        out["requeued"] += 1
                    else:
                        conn.execute("UPDATE ops.jobs SET status = 'failed', error = %s, result_path = NULL "
                                     "WHERE job_id = %s", (encode_error(WORKER_LOST_ERROR), r["job_id"]))
                        out["failed"] += 1
        return out

    def expire_queued(self, ttl_seconds: float) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ops.jobs SET status = 'failed', error = %s WHERE status = 'queued' AND execution = 'async' "
                "AND created_at < now() - make_interval(secs => %s)", (encode_error(EXPIRED_ERROR), ttl_seconds))
            return cur.rowcount
