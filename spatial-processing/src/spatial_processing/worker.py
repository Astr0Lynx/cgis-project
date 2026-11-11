# In-process background workers for async jobs (PostGIS ops.jobs queue, no Redis). Each thread loops:
# maintenance (re-queue/fail jobs whose worker stopped heart-beating, expire jobs queued past the TTL)
# -> claim the oldest queued job (FOR UPDATE SKIP LOCKED) -> run it -> repeat; idle threads sleep
# worker_poll_seconds or until a job is queued in this process. Several replicas may share one queue.

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

KINDS = ("zonal-statistics", "clip")


# ASYNC_WORKERS daemon threads running async jobs through a ProcessingService.
# Created, started and stopped by app.create_app (lifespan).
class WorkerPool:
    # Keep the service and per-pool state.
    def __init__(self, service):
        self.service = service
        self.settings = service.settings
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_maintenance = 0.0
        self._maint_lock = threading.Lock()
        self.processed = 0
        self.last_loop = 0.0

    # Start the threads. Async support of the job store (migration core/0009) is re-checked in the
    # loop, so a database that is not yet reachable at start-up does not disable the queue.
    def start(self) -> None:
        if self.settings.async_workers < 1:
            log.info("async workers disabled (ASYNC_WORKERS=0)")
            return
        for i in range(self.settings.async_workers):
            t = threading.Thread(target=self._run, name=f"async-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("started %d async worker(s) as %s", len(self._threads), self.service.worker_id)

    # Signal the threads to stop and wait for them. Used by the app lifespan and tests.
    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self.service.wake.set()
        for t in self._threads:
            t.join(timeout=timeout)

    # Number of live worker threads. Used by app.ready.
    @property
    def alive(self) -> int:
        return sum(t.is_alive() for t in self._threads)

    # Recover stale running jobs and expire old queued ones (at most every max(heartbeat, 5) s unless
    # forced); returns the counts. Used by run_once and tests.
    def maintenance(self, force: bool = False) -> dict[str, int]:
        now = time.monotonic()
        with self._maint_lock:
            if not force and now - self._last_maintenance < max(self.settings.worker_heartbeat_seconds, 5.0):
                return {}
            self._last_maintenance = now
        out = dict(self.service.jobs.recover_stale(self.settings.worker_stale_seconds))
        out["expired"] = self.service.jobs.expire_queued(self.settings.async_queue_ttl_seconds)
        if any(out.values()):
            log.warning("job maintenance: %s", out)
        return out

    # Maintenance plus at most one job; True if a job was processed. Used by _run and tests.
    def run_once(self) -> bool:
        self.maintenance()
        job = self.service.jobs.claim(self.service.worker_id, KINDS)
        if job is None:
            return False
        log.info("worker claimed job %s (%s, attempt %d/%d)", job.job_id, job.kind, job.attempts, job.max_attempts)
        self.service.execute_async(job)
        self.processed += 1
        return True

    # Thread body: loop until stopped; errors (e.g. job store down) back off and never kill the thread.
    def _run(self) -> None:
        while not self._stop.is_set():
            self.last_loop = time.monotonic()
            try:
                worked = self.service.async_enabled and self.run_once()
            except Exception:
                log.exception("async worker loop error; retrying")
                worked = False
            if not worked:
                self.service.wake.wait(self.settings.worker_poll_seconds)
                self.service.wake.clear()
