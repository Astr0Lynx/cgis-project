# Storage backends: the dataset catalog (PostGIS catalog.current_datasets, in-memory, or a
# static JSON file) and the job store (PostGIS ops.jobs, or in-memory). Clients only ever send
# a dataset ID; raster file locations come solely from the catalog.

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ApiError

# --------------------------------------------------------------------------- shared


# Open a psycopg connection returning dict rows. Used by PostgresCatalog and PostgresJobStore.
def _connect(conninfo: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(conninfo or "", row_factory=dict_row, connect_timeout=5)


# --------------------------------------------------------------------------- catalog

# Logical slug: letters, digits, '.', '_', '-'; starts alphanumeric. Rejects paths, URLs, whitespace.
DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_CATALOG_COLUMNS = (
    "dataset_id, version, title, kind, product_level, year, crs, units, nodata, "
    "asset_path, checksum_sha256, source, provenance, status"
)


@dataclass(frozen=True)
class DatasetRecord:
    dataset_id: str
    version: int
    kind: str
    status: str
    crs: str | None
    units: str | None
    nodata: float | None
    asset_path: str | None
    title: str = ""
    year: int | None = None
    product_level: str | None = None
    source: str | None = None
    checksum_sha256: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    # Metadata safe to echo to clients (no server-side path). Used by service results and sidecars.
    def public_metadata(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "title": self.title,
            "year": self.year,
            "product_level": self.product_level,
            "units": self.units,
            "catalog_crs": self.crs,
            "catalog_nodata": self.nodata,
            "source": self.source,
            "checksum_sha256": self.checksum_sha256,
            "provenance": self.provenance,
        }


# Raise 422 INVALID_DATASET_ID unless `dataset_id` is a catalog slug.
# Used by both catalogs' get_ready and service._validate.
def validate_dataset_id(dataset_id: str) -> None:
    if not isinstance(dataset_id, str) or not DATASET_ID_RE.match(dataset_id):
        raise ApiError(
            422,
            "INVALID_DATASET_ID",
            "dataset_id must be a catalog slug (letters, digits, '.', '_', '-'; max 128 chars); "
            "paths and URLs are not accepted",
        )


# Return the record if it is a ready raster, else raise 409/422. Used by both catalogs' get_ready.
def _check_record(record: DatasetRecord) -> DatasetRecord:
    if record.status != "ready":
        raise ApiError(
            409,
            "DATASET_NOT_READY",
            f"dataset '{record.dataset_id}' is not ready (status: {record.status})",
            {"dataset_id": record.dataset_id, "status": record.status},
        )
    if record.kind != "raster":
        raise ApiError(
            422,
            "DATASET_NOT_RASTER",
            f"dataset '{record.dataset_id}' is a {record.kind} dataset; raster operations need a raster",
            {"dataset_id": record.dataset_id, "kind": record.kind},
        )
    return record


# Build the 404 error for an unknown dataset. Used by both catalogs' get_ready.
def _not_found(dataset_id: str) -> ApiError:
    return ApiError(404, "DATASET_NOT_FOUND", f"dataset '{dataset_id}' was not found in the catalog")


# Build a DatasetRecord from a catalog.datasets row (dict). Used by PostgresCatalog and
# load_static_catalog.
def record_from_row(row: dict[str, Any]) -> DatasetRecord:
    provenance = row.get("provenance") or {}
    if isinstance(provenance, str):
        provenance = json.loads(provenance)
    nodata = row.get("nodata")
    return DatasetRecord(
        dataset_id=row["dataset_id"],
        version=int(row["version"]),
        kind=row["kind"],
        status=row["status"],
        crs=row.get("crs"),
        units=row.get("units"),
        nodata=float(nodata) if nodata is not None else None,
        asset_path=row.get("asset_path"),
        title=row.get("title") or "",
        year=row.get("year"),
        product_level=row.get("product_level"),
        source=row.get("source"),
        checksum_sha256=row.get("checksum_sha256"),
        provenance=provenance,
    )


# Catalog over a list of records with the same semantics as the current_datasets view
# (highest ready version wins). Used by tests and the memory/static backends.
class InMemoryCatalog:
    # Keep a copy of the records.
    def __init__(self, records: list[DatasetRecord] | None = None):
        self._records = list(records or [])

    # Add a record (e.g. a new version). Used by tests.
    def add(self, record: DatasetRecord) -> None:
        self._records.append(record)

    # Return the current ready raster record or raise ApiError. Used by service._validate.
    def get_ready(self, dataset_id: str) -> DatasetRecord:
        validate_dataset_id(dataset_id)
        versions = [r for r in self._records if r.dataset_id == dataset_id]
        if not versions:
            raise _not_found(dataset_id)
        ready = [r for r in versions if r.status == "ready"]
        return _check_record(max(ready or versions, key=lambda r: r.version))

    # Always reachable. Used by service.readiness.
    def ping(self) -> None:
        return None


# Load a JSON list of catalog rows (same columns as catalog.datasets, e.g.
# [{"dataset_id": "x", "version": 1, "kind": "raster", "status": "ready", "crs": "EPSG:4326",
#   "units": "people_per_cell", "nodata": -9999, "asset_path": "x.tif"}]).
# Used by app.build_catalog for CATALOG_BACKEND=static with STATIC_CATALOG_FILE.
def load_static_catalog(path: Path) -> InMemoryCatalog:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return InMemoryCatalog([record_from_row(row) for row in rows])


# Reads catalog.current_datasets (highest ready version) with parameterised SQL only.
# Used by app.build_catalog for CATALOG_BACKEND=postgres.
class PostgresCatalog:
    # Remember the connection string.
    def __init__(self, conninfo: str | None):
        self._conninfo = conninfo

    # Return the current ready raster record or raise ApiError (503 if the DB is unreachable).
    # Used by service._validate.
    def get_ready(self, dataset_id: str) -> DatasetRecord:
        validate_dataset_id(dataset_id)
        try:
            with _connect(self._conninfo) as conn:
                row = conn.execute(
                    f"SELECT {_CATALOG_COLUMNS} FROM catalog.current_datasets WHERE dataset_id = %s",
                    (dataset_id,),
                ).fetchone()
                if row is None:
                    # Distinguish "unknown" from "exists but nothing ready" for a clearer error.
                    row = conn.execute(
                        f"SELECT {_CATALOG_COLUMNS} FROM catalog.datasets WHERE dataset_id = %s "
                        "ORDER BY version DESC LIMIT 1",
                        (dataset_id,),
                    ).fetchone()
        except Exception as exc:  # psycopg.Error, network errors
            raise ApiError(503, "CATALOG_UNAVAILABLE", "dataset catalog is unavailable") from exc
        if row is None:
            raise _not_found(dataset_id)
        return _check_record(record_from_row(dict(row)))

    # Raise if the catalog view is unreachable. Used by service.readiness.
    def ping(self) -> None:
        with _connect(self._conninfo) as conn:
            conn.execute("SELECT 1 FROM catalog.current_datasets LIMIT 0")


# --------------------------------------------------------------------------- jobs
# Status lifecycle: queued -> running -> completed, queued -> failed, running -> failed, and for
# async jobs only running -> queued (a lost worker's job is retried). result_path is relative to the
# output root; error is a {"code", "message"} object (stored JSON-encoded in ops.jobs.error).
# Sync jobs run inside their HTTP request. Async jobs (columns of migration core/0009) are claimed by
# a background worker with FOR UPDATE SKIP LOCKED; the worker refreshes heartbeat_at/progress while
# running and honours cancel_requested at its next checkpoint.

QUEUED, RUNNING, COMPLETED, FAILED = "queued", "running", "completed", "failed"
SYNC, ASYNC = "sync", "async"
_ALLOWED = {QUEUED: {RUNNING, FAILED}, RUNNING: {COMPLETED, FAILED}, COMPLETED: set(), FAILED: set()}
_JOB_COLUMNS = "job_id, kind, status, input, result_path, error, created_at, updated_at"
_ASYNC_FIELDS = (
    "execution", "attempts", "max_attempts", "progress", "progress_message", "cancel_requested", "heartbeat_at",
    "worker_id",
)
_ASYNC_JOB_COLUMNS = f"{_JOB_COLUMNS}, {', '.join(_ASYNC_FIELDS)}"

# Errors recorded on async jobs that end without running to completion.
WORKER_LOST_ERROR = {"code": "WORKER_LOST",
                     "message": "the worker running this job stopped (restart or crash) and retries are exhausted"}
EXPIRED_ERROR = {"code": "JOB_EXPIRED", "message": "the job waited in the queue longer than the queue TTL"}
CANCELLED_ERROR = {"code": "CANCELLED", "message": "the job was cancelled at the client's request"}


class InvalidTransition(RuntimeError):
    pass


# Current UTC time. Used for Job timestamps in the in-memory store.
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


# Raise InvalidTransition unless old -> new is allowed. Used by both job stores' transition.
def _check_transition(old: str, new: str, execution: str) -> None:
    if old == RUNNING and new == QUEUED and execution == ASYNC:
        return
    if new not in _ALLOWED.get(old, set()):
        raise InvalidTransition(f"job status cannot change from {old} to {new}")


# JSON-encode an error object for ops.jobs.error. Used by PostgresJobStore.
def _encode_error(error: dict[str, Any] | None) -> str | None:
    return json.dumps(error, sort_keys=True) if error else None


# Decode the stored error text (JSON object, or legacy plain text). Used by PostgresJobStore._row_to_job.
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


# Thread-safe job store that also records each job's status history (for tests).
# Used by tests and JOB_STORE_BACKEND=memory.
class InMemoryJobStore:
    supports_async = True

    # Start with no jobs.
    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, Job] = {}
        self._lock = threading.Lock()
        self.history: dict[uuid.UUID, list[str]] = {}

    # Replace a job and append its new status to the history when it changed. Caller holds the lock.
    def _put(self, job: Job) -> Job:
        if job.status != self._jobs[job.job_id].status:
            self.history[job.job_id].append(job.status)
        self._jobs[job.job_id] = job
        return job

    # Create a queued job. Used by service._run and service._enqueue.
    def create(self, kind: str, input: dict[str, Any], *, execution: str = SYNC, max_attempts: int = 1) -> Job:
        job = Job(job_id=uuid.uuid4(), kind=kind, status=QUEUED, input=input, execution=execution,
                  max_attempts=max_attempts)
        with self._lock:
            self._jobs[job.job_id] = job
            self.history[job.job_id] = [QUEUED]
        return job

    # Move a job to `status`, optionally setting result_path/error. Used by service._run/_fail/cancel_job.
    def transition(self, job_id, status, *, result_path=None, error=None) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            _check_transition(job.status, status, job.execution)
            self.history[job_id].append(status)
            job = replace(
                job,
                status=status,
                result_path=result_path if result_path is not None else job.result_path,
                error=error if error is not None else job.error,
                progress=1.0 if status == COMPLETED else job.progress,
                updated_at=_utcnow(),
            )
            self._jobs[job_id] = job
            return job

    # Return the job or None. Used by service._get.
    def get(self, job_id: uuid.UUID) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    # Always reachable. Used by service.readiness.
    def ping(self) -> None:
        return None

    # Claim the oldest queued async job of `kinds` (running, attempts + 1). Used by worker.WorkerPool.run_once.
    def claim(self, worker_id: str, kinds: tuple[str, ...]) -> Job | None:
        with self._lock:
            queued = [j for j in self._jobs.values()
                      if j.status == QUEUED and j.execution == ASYNC and j.kind in kinds and not j.cancel_requested]
            if not queued:
                return None
            job = min(queued, key=lambda j: j.created_at)
            return self._put(replace(job, status=RUNNING, attempts=job.attempts + 1, worker_id=worker_id,
                                     heartbeat_at=_utcnow(), updated_at=_utcnow()))

    # Record progress of a running job; returns True when cancellation was requested.
    # Used by service.execute_async.
    def heartbeat(self, job_id, progress, message) -> bool:
        with self._lock:
            job = self._jobs[job_id]
            if job.status == RUNNING:
                self._jobs[job_id] = replace(job, progress=progress if progress is not None else job.progress,
                                             progress_message=message or job.progress_message,
                                             heartbeat_at=_utcnow(), updated_at=_utcnow())
            return job.cancel_requested

    # Flag a queued/running job for cancellation; returns the job (unchanged if finished) or None.
    # Used by service.cancel_job.
    def request_cancel(self, job_id) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in (COMPLETED, FAILED):
                return job
            job = replace(job, cancel_requested=True, updated_at=_utcnow())
            self._jobs[job_id] = job
            return job

    # Re-queue (attempts left) or fail (WORKER_LOST) running async jobs without a recent heartbeat.
    # Used by worker.WorkerPool.maintenance.
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
                    self._put(replace(job, status=QUEUED, worker_id=None, updated_at=_utcnow()))
                    out["requeued"] += 1
                else:
                    self._put(replace(job, status=FAILED, error=WORKER_LOST_ERROR, updated_at=_utcnow()))
                    out["failed"] += 1
        return out

    # Fail (JOB_EXPIRED) async jobs queued longer than `ttl_seconds`. Used by worker.WorkerPool.maintenance.
    def expire_queued(self, ttl_seconds: float) -> int:
        cutoff = _utcnow() - timedelta(seconds=ttl_seconds)
        n = 0
        with self._lock:
            for job in list(self._jobs.values()):
                if job.status == QUEUED and job.execution == ASYNC and job.created_at < cutoff:
                    self._put(replace(job, status=FAILED, error=EXPIRED_ERROR, updated_at=_utcnow()))
                    n += 1
        return n

    # Test helper: move a job's created_at back and/or set its heartbeat age. Used by test_async_blockwise.
    def age(self, job_id: uuid.UUID, *, created_seconds: float = 0, heartbeat_seconds: float | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            changes: dict[str, Any] = {"created_at": job.created_at - timedelta(seconds=created_seconds)}
            if heartbeat_seconds is not None:
                changes["heartbeat_at"] = _utcnow() - timedelta(seconds=heartbeat_seconds)
            self._jobs[job_id] = replace(job, **changes)


# Job store over ops.jobs; async jobs need the columns of database migration core/0009 (detected
# at runtime, so the store also works on an older schema for sync jobs).
# Used by app.build_job_store for JOB_STORE_BACKEND=postgres.
class PostgresJobStore:
    # Remember the connection string.
    def __init__(self, conninfo: str | None):
        self._conninfo = conninfo
        self._async: bool | None = None

    # Whether ops.jobs has the async columns (cached once the database answered). Used by the
    # service (async_enabled) and the queries below.
    @property
    def supports_async(self) -> bool:
        if self._async is None:
            try:
                with _connect(self._conninfo) as conn:
                    row = conn.execute(
                        "SELECT count(*) AS n FROM information_schema.columns WHERE table_schema = 'ops' "
                        "AND table_name = 'jobs' AND column_name IN ('execution', 'heartbeat_at', "
                        "'cancel_requested', 'attempts')").fetchone()
                self._async = row["n"] == 4
            except Exception:
                return False
        return self._async

    # Columns to select: with the async columns when the schema has them. Used by the queries below.
    @property
    def _cols(self) -> str:
        return _ASYNC_JOB_COLUMNS if self.supports_async else _JOB_COLUMNS

    # Convert an ops.jobs row to a Job. Used by every query below.
    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> Job:
        payload = row["input"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        extra = {k: row[k] for k in _ASYNC_FIELDS} if "execution" in row else {}
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

    # Insert a queued job (with execution/max_attempts when async). Used by service._run and service._enqueue.
    def create(self, kind: str, input: dict[str, Any], *, execution: str = SYNC, max_attempts: int = 1) -> Job:
        from psycopg.types.json import Jsonb

        job_id = uuid.uuid4()
        with _connect(self._conninfo) as conn:
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

    # Move a job to `status` under a row lock, optionally setting result_path/error (progress = 1 on
    # completion). Used by service._run/_fail/execute_async/cancel_job.
    def transition(self, job_id, status, *, result_path=None, error=None) -> Job:
        with _connect(self._conninfo) as conn, conn.transaction():
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
                (status, result_path, _encode_error(error), job_id),
            ).fetchone()
        return self._row_to_job(row)

    # Return the job or None. Used by service._get.
    def get(self, job_id: uuid.UUID) -> Job | None:
        with _connect(self._conninfo) as conn:
            row = conn.execute(f"SELECT {self._cols} FROM ops.jobs WHERE job_id = %s", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    # Raise if ops.jobs is unreachable. Used by service.readiness.
    def ping(self) -> None:
        with _connect(self._conninfo) as conn:
            conn.execute("SELECT 1 FROM ops.jobs LIMIT 0")

    # Claim the oldest queued async job of `kinds`; FOR UPDATE SKIP LOCKED so concurrent workers
    # (threads or replicas) never get the same job. Used by worker.WorkerPool.run_once.
    def claim(self, worker_id: str, kinds: tuple[str, ...]) -> Job | None:
        with _connect(self._conninfo) as conn:
            row = conn.execute(
                "UPDATE ops.jobs SET status = 'running', attempts = attempts + 1, worker_id = %s, "
                "heartbeat_at = now(), progress = 0 "
                "WHERE job_id = (SELECT job_id FROM ops.jobs WHERE status = 'queued' AND execution = 'async' "
                "AND kind = ANY(%s) AND NOT cancel_requested ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1) "
                f"RETURNING {_ASYNC_JOB_COLUMNS}",
                (worker_id, list(kinds)),
            ).fetchone()
        return self._row_to_job(row) if row else None

    # Record progress of a running job; returns True when cancellation was requested.
    # Used by service.execute_async.
    def heartbeat(self, job_id, progress, message) -> bool:
        with _connect(self._conninfo) as conn:
            row = conn.execute(
                "UPDATE ops.jobs SET heartbeat_at = now(), progress = COALESCE(%s, progress), "
                "progress_message = COALESCE(%s, progress_message) "
                "WHERE job_id = %s AND status = 'running' RETURNING cancel_requested",
                (progress, message, job_id),
            ).fetchone()
            if row is None:
                row = conn.execute("SELECT cancel_requested FROM ops.jobs WHERE job_id = %s", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    # Flag a queued/running job for cancellation; returns the job (unchanged if finished) or None.
    # Used by service.cancel_job.
    def request_cancel(self, job_id) -> Job | None:
        with _connect(self._conninfo) as conn:
            row = conn.execute(
                "UPDATE ops.jobs SET cancel_requested = true WHERE job_id = %s AND status IN ('queued', 'running') "
                f"RETURNING {_ASYNC_JOB_COLUMNS}", (job_id,)).fetchone()
            if row is None:
                row = conn.execute(f"SELECT {_ASYNC_JOB_COLUMNS} FROM ops.jobs WHERE job_id = %s", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    # Re-queue (attempts left) or fail (WORKER_LOST) running async jobs without a recent heartbeat.
    # Used by worker.WorkerPool.maintenance.
    def recover_stale(self, stale_seconds: float) -> dict[str, int]:
        out = {"requeued": 0, "failed": 0}
        with _connect(self._conninfo) as conn, conn.transaction():
            stale = conn.execute(
                "SELECT job_id, attempts, max_attempts, cancel_requested FROM ops.jobs "
                "WHERE status = 'running' AND execution = 'async' "
                "AND (heartbeat_at IS NULL OR heartbeat_at < now() - make_interval(secs => %s)) "
                "FOR UPDATE SKIP LOCKED", (stale_seconds,)).fetchall()
            for r in stale:
                if r["attempts"] < r["max_attempts"] and not r["cancel_requested"]:
                    conn.execute("UPDATE ops.jobs SET status = 'queued', worker_id = NULL WHERE job_id = %s",
                                 (r["job_id"],))
                    out["requeued"] += 1
                else:
                    conn.execute("UPDATE ops.jobs SET status = 'failed', error = %s, result_path = NULL "
                                 "WHERE job_id = %s", (_encode_error(WORKER_LOST_ERROR), r["job_id"]))
                    out["failed"] += 1
        return out

    # Fail (JOB_EXPIRED) async jobs queued longer than `ttl_seconds`. Used by worker.WorkerPool.maintenance.
    def expire_queued(self, ttl_seconds: float) -> int:
        with _connect(self._conninfo) as conn:
            cur = conn.execute(
                "UPDATE ops.jobs SET status = 'failed', error = %s WHERE status = 'queued' AND execution = 'async' "
                "AND created_at < now() - make_interval(secs => %s)", (_encode_error(EXPIRED_ERROR), ttl_seconds))
            return cur.rowcount
