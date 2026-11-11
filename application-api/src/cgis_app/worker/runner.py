# Worker loop: claim a job, keep its lease alive from a heartbeat thread, run the handler, record the outcome.
# Cancel: handler stops at its next progress check. SIGTERM: no new claims; a running export is re-queued.
# Crash: the lease expires and jobs.claim hands the job to the next worker (up to max_attempts).

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import jobs
from ..config import Settings
from ..db import connect

log = logging.getLogger("cgis_app.worker")


HEALTH_FILE_ENV = "APP_WORKER_HEALTH_FILE"
DEFAULT_HEALTH_FILE = "/tmp/cgis-worker.alive"


# Touch the liveness file (APP_WORKER_HEALTH_FILE). Used by the poll loop and heartbeats.
def touch_health() -> None:
    path = os.environ.get(HEALTH_FILE_ENV, DEFAULT_HEALTH_FILE)
    try:
        with open(path, "a"):
            pass
        os.utime(path, None)
    except OSError:
        pass


# True when the liveness file is fresh. Used by `cgis-app worker-health`.
def health_ok(max_age_seconds: float = 120.0) -> bool:
    try:
        return time.time() - os.stat(os.environ.get(HEALTH_FILE_ENV, DEFAULT_HEALTH_FILE)).st_mtime < max_age_seconds
    except OSError:
        return False


# User-presentable job failure {code, message, details?}. Raised by handlers, recorded by run_one.
class JobFailure(Exception):
    # Build the error dict.
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.error = {"code": code, "message": message}
        if details:
            self.error["details"] = details


# Raised by JobContext.check when cancel was requested.
class Cancelled(Exception):
    pass


# Raised by JobContext.check on worker shutdown (export jobs are released for retry).
class Interrupted(Exception):
    pass


# Raised by JobContext.check when another worker took over the lease.
class LeaseLost(Exception):
    pass


# Successful handler result stored by jobs.complete.
@dataclass
class Outcome:
    result: dict[str, Any]
    result_path: str | None = None
    result_size: int | None = None
    result_sha256: str | None = None
    expires_at: Any = None


# Thread extending the lease and writing progress (at most twice a second); notes cancel/lease loss.
class Heartbeat(threading.Thread):
    # Set up state; interval is a quarter of the lease (1-10 s).
    def __init__(self, dsn: str, job_id: uuid.UUID, worker_id: str, lease_seconds: int):
        super().__init__(name=f"heartbeat-{job_id}", daemon=True)
        self.dsn = dsn
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease = lease_seconds
        self.interval = max(1.0, min(10.0, lease_seconds / 4))
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._progress: float | None = None
        self._message: str | None = None
        self._dirty = threading.Event()
        self.cancel_requested = False
        self.lost = False

    # Queue a progress update. Used by JobContext.progress.
    def update(self, progress: float | None, message: str | None) -> None:
        with self._lock:
            self._progress, self._message = progress, message
        self._dirty.set()

    # Ask the thread to exit. Used by run_one/_stop.
    def stop(self) -> None:
        self._halt.set()
        self._dirty.set()

    # Thread body: own connection, periodic jobs.heartbeat.
    def run(self) -> None:
        try:
            conn = connect(self.dsn, application_name="cgis-app-heartbeat", wait_seconds=10)
        except Exception:
            log.exception("heartbeat cannot connect; the lease will expire if the job outlives it")
            return
        try:
            while not self._halt.is_set():
                # Wake on a progress update or at least every `interval` seconds to extend the lease.
                self._dirty.wait(timeout=self.interval)
                if self._halt.is_set():
                    break
                self._dirty.clear()
                with self._lock:
                    progress, message = self._progress, self._message
                try:
                    self.cancel_requested = jobs.heartbeat(conn, self.job_id, self.worker_id, lease_seconds=self.lease,
                                                           progress=progress, message=message)
                    touch_health()
                except LookupError:
                    self.lost = True
                    return
                except Exception:
                    log.exception("heartbeat failed for job %s", self.job_id)
                self._halt.wait(0.5)  # at most two progress writes per second
        finally:
            conn.close()


# What a handler gets: job row, settings, connection, heartbeat, cleanup callbacks.
@dataclass
class JobContext:
    job: dict
    settings: Settings
    conn: Any
    worker_id: str
    heartbeat: Heartbeat
    stopping: threading.Event
    dsn: str = ""
    deadline: float = 0.0
    cleanup: list[Callable[[], None]] = field(default_factory=list)

    # Report progress, then check for cancel/shutdown/timeout. Used by the handlers.
    def progress(self, fraction: float | None, message: str | None = None) -> None:
        self.heartbeat.update(fraction, message)
        self.check()

    # Raise LeaseLost, Cancelled, Interrupted (exports) or JOB_TIMEOUT as appropriate.
    def check(self, stage: str = "") -> None:
        if self.heartbeat.lost:
            raise LeaseLost(str(self.job["job_id"]))
        if self.heartbeat.cancel_requested:
            raise Cancelled()
        if self.stopping.is_set() and self.job["queue"] == "export":
            raise Interrupted()
        if self.deadline and time.monotonic() > self.deadline:
            raise JobFailure("JOB_TIMEOUT", f"the job exceeded its {self.settings.export_max_seconds}s time limit"
                                            + (f" (stage: {stage})" if stage else ""))


Handler = Callable[[JobContext], Outcome]


# Unique worker id host:pid:queue:random. Used by serve.
def worker_id(queue: str) -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{queue}:{uuid.uuid4().hex[:6]}"


# Claim and run at most one job, recording completed/cancelled/requeued/failed; False when empty.
# Used by serve.
def run_one(conn, dsn: str, settings: Settings, queue: str, handler: Handler, wid: str,
            stopping: threading.Event) -> bool:
    job = jobs.claim(conn, queue, wid, settings.job_lease_seconds)
    if job is None:
        return False
    job_id = job["job_id"]
    log.info("job %s (%s/%s) claimed, attempt %s", job_id, queue, job["kind"], job["attempts"])
    hb = Heartbeat(dsn, job_id, wid, settings.job_lease_seconds)
    hb.start()
    ctx = JobContext(job=job, settings=settings, conn=conn, worker_id=wid, heartbeat=hb, stopping=stopping, dsn=dsn,
                     deadline=time.monotonic() + settings.export_max_seconds if queue == "export" else 0.0)
    started = time.monotonic()
    try:
        outcome = handler(ctx)
        hb.stop()
        hb.join(timeout=5)
        if jobs.complete(conn, job_id, wid, result=outcome.result, result_path=outcome.result_path,
                         result_size=outcome.result_size, result_sha256=outcome.result_sha256,
                         expires_at=outcome.expires_at) is None:
            log.warning("job %s finished but its lease was lost; result discarded", job_id)
            _run_cleanup(ctx)
        else:
            log.info("job %s completed in %.2fs", job_id, time.monotonic() - started)
    except Cancelled:
        _stop(hb)
        _run_cleanup(ctx)
        jobs.mark_cancelled(conn, job_id, wid)
        log.info("job %s cancelled", job_id)
    except Interrupted:
        _stop(hb)
        _run_cleanup(ctx)
        conn.execute("UPDATE cgis_app.jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, "
                     "progress = NULL, progress_message = 'requeued after worker shutdown' WHERE job_id = %s AND "
                     "lease_owner = %s AND status = 'running'", (job_id, wid))
        log.info("job %s released for retry (worker shutting down)", job_id)
    except LeaseLost:
        _stop(hb)
        _run_cleanup(ctx)
        log.warning("job %s: lease lost to another worker; stopped", job_id)
    except JobFailure as exc:
        _stop(hb)
        _run_cleanup(ctx)
        jobs.fail(conn, job_id, wid, exc.error)
        log.info("job %s failed [%s] %s", job_id, exc.error["code"], exc.error["message"])
    except Exception:
        _stop(hb)
        _run_cleanup(ctx)
        log.exception("job %s crashed", job_id)
        jobs.fail(conn, job_id, wid, {"code": "INTERNAL_ERROR", "message": "internal error while running the job"})
    return True


# Stop and join a heartbeat. Used by run_one.
def _stop(hb: Heartbeat) -> None:
    hb.stop()
    hb.join(timeout=5)


# Run a job's cleanup callbacks in reverse (errors logged). Used by run_one.
def _run_cleanup(ctx: JobContext) -> None:
    for fn in reversed(ctx.cleanup):
        try:
            fn()
        except Exception:
            log.exception("cleanup failed for job %s", ctx.job["job_id"])


# Worker main loop until SIGTERM/SIGINT (or empty queue with once); pins GDAL cache, runs maintenance
# every minute. Used by `cgis-app worker` and the tests.
def serve(settings: Settings, queue: str, *, dsn: str | None = None, once: bool = False,
          maintenance: Callable[[Any, Settings], None] | None = None, handler: Handler | None = None,
          stopping: threading.Event | None = None) -> int:
    if handler is None:
        if queue == "ingest":
            from .ingest import handle as handler, maintenance as default_maintenance
        else:
            from .export import handle as handler, maintenance as default_maintenance
        maintenance = maintenance or default_maintenance
    dsn = dsn if dsn is not None else settings.database_url
    # Bound GDAL's block cache for EVERY raster call in this process, including the importer's (which runs
    # outside any rasterio.Env); GDAL's default is 5% of physical RAM. Value in MB.
    os.environ.setdefault("GDAL_CACHEMAX", str(settings.gdal_cachemax_mb))
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    stopping = stopping or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopping.set())
    wid = worker_id(queue)
    conn = connect(dsn, application_name=f"cgis-app-{queue}-worker", wait_seconds=60)
    log.info("worker %s started", wid)
    last_maintenance = 0.0
    processed = 0
    try:
        while not stopping.is_set():
            touch_health()
            if maintenance and time.monotonic() - last_maintenance > 60:
                last_maintenance = time.monotonic()
                try:
                    maintenance(conn, settings)
                except Exception:
                    log.exception("maintenance failed")
            if run_one(conn, dsn, settings, queue, handler, wid, stopping):
                processed += 1
                continue
            if once:
                break
            stopping.wait(settings.worker_poll_seconds)
    finally:
        conn.close()
        log.info("worker %s stopped after %d job(s)", wid, processed)
    return processed
