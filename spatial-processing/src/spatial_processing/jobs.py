"""Job records (``ops.jobs`` in PostGIS, or in memory for tests/dev).

Status values and allowed transitions::

    queued -> running -> completed
    queued -> failed, running -> failed

``result_path`` is relative to the output root (never an absolute server path) and
``error`` holds a JSON-encoded ``{"code", "message"}`` object.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

QUEUED, RUNNING, COMPLETED, FAILED = "queued", "running", "completed", "failed"
STATUSES = (QUEUED, RUNNING, COMPLETED, FAILED)
_ALLOWED = {
    QUEUED: {RUNNING, FAILED},
    RUNNING: {COMPLETED, FAILED},
    COMPLETED: set(),
    FAILED: set(),
}


class InvalidTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    job_id: uuid.UUID
    kind: str
    status: str
    input: dict[str, Any]
    result_path: str | None = None
    error: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _check_transition(old: str, new: str) -> None:
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
    def create(self, kind: str, input: dict[str, Any]) -> Job: ...

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


class InMemoryJobStore:
    """Thread-safe store; keeps a transition history for tests."""

    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, Job] = {}
        self._lock = threading.Lock()
        self.history: dict[uuid.UUID, list[str]] = {}

    def create(self, kind: str, input: dict[str, Any]) -> Job:
        job = Job(job_id=uuid.uuid4(), kind=kind, status=QUEUED, input=input)
        with self._lock:
            self._jobs[job.job_id] = job
            self.history[job.job_id] = [QUEUED]
        return job

    def transition(self, job_id, status, *, result_path=None, error=None) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            _check_transition(job.status, status)
            job = replace(
                job,
                status=status,
                result_path=result_path if result_path is not None else job.result_path,
                error=error if error is not None else job.error,
                updated_at=datetime.now(timezone.utc),
            )
            self._jobs[job_id] = job
            self.history[job_id].append(status)
            return job

    def get(self, job_id: uuid.UUID) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def ping(self) -> None:
        return None


class PostgresJobStore:
    """``ops.jobs(job_id UUID, kind, status, input JSONB, result_path, error, created_at, updated_at)``."""

    def __init__(self, conninfo: str | None):
        self._conninfo = conninfo or ""

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._conninfo, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> Job:
        payload = row["input"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return Job(
            job_id=row["job_id"],
            kind=row["kind"],
            status=row["status"],
            input=payload or {},
            result_path=row["result_path"],
            error=decode_error(row["error"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create(self, kind: str, input: dict[str, Any]) -> Job:
        from psycopg.types.json import Jsonb

        job_id = uuid.uuid4()
        with self._connect() as conn:
            row = conn.execute(
                "INSERT INTO ops.jobs (job_id, kind, status, input, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, now(), now()) "
                "RETURNING job_id, kind, status, input, result_path, error, created_at, updated_at",
                (job_id, kind, QUEUED, Jsonb(input)),
            ).fetchone()
        return self._row_to_job(row)

    def transition(self, job_id, status, *, result_path=None, error=None) -> Job:
        with self._connect() as conn:
            with conn.transaction():
                current = conn.execute(
                    "SELECT status FROM ops.jobs WHERE job_id = %s FOR UPDATE", (job_id,)
                ).fetchone()
                if current is None:
                    raise KeyError(str(job_id))
                _check_transition(current["status"], status)
                row = conn.execute(
                    "UPDATE ops.jobs SET status = %s, result_path = COALESCE(%s, result_path), "
                    "error = COALESCE(%s, error), updated_at = now() WHERE job_id = %s "
                    "RETURNING job_id, kind, status, input, result_path, error, created_at, updated_at",
                    (status, result_path, encode_error(error), job_id),
                ).fetchone()
        return self._row_to_job(row)

    def get(self, job_id: uuid.UUID) -> Job | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, kind, status, input, result_path, error, created_at, updated_at "
                "FROM ops.jobs WHERE job_id = %s",
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1 FROM ops.jobs LIMIT 0")
