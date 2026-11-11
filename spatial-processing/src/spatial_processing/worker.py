"""In-process background workers for ``execution = 'async'`` jobs (local PostGIS queue, no Redis).

Each worker thread loops: maintenance (re-queue or fail jobs whose worker stopped heart-beating;
fail jobs queued longer than the queue TTL) -> claim the oldest queued job with
``FOR UPDATE SKIP LOCKED`` -> run it to a terminal state -> repeat. Idle workers sleep
``worker_poll_seconds`` or until a new job is queued in this process.

Capacity is explicit: ``async_workers`` threads (default 1) each hold at most one block of
``block_size^2`` pixels plus the GDAL cache (GDAL_CACHEMAX_MB); several processing replicas may
share one queue safely. After a restart, jobs left ``running`` by the old process stop
heart-beating and are re-queued (attempts < max_attempts) or failed with WORKER_LOST.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

KINDS = ("zonal-statistics", "clip")


class WorkerPool:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_maintenance = 0.0
        self._maint_lock = threading.Lock()
        self.processed = 0
        self.last_loop = 0.0

    def start(self) -> None:
        # Threads start whenever workers are configured; whether the job store supports async jobs
        # (database migration core/0009) is re-checked in the loop, so a database that is not yet
        # reachable at start-up does not disable the queue for the life of the process.
        if self.settings.async_workers < 1:
            log.info("async workers disabled (ASYNC_WORKERS=0)")
            return
        for i in range(self.settings.async_workers):
            t = threading.Thread(target=self._run, name=f"async-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("started %d async worker(s) as %s", len(self._threads), self.service.worker_id)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self.service.wake.set()
        for t in self._threads:
            t.join(timeout=timeout)

    @property
    def alive(self) -> int:
        return sum(t.is_alive() for t in self._threads)

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

    def run_once(self) -> bool:
        """Maintenance + at most one job. Returns True if a job was processed (used by tests)."""
        self.maintenance()
        job = self.service.jobs.claim(self.service.worker_id, KINDS)
        if job is None:
            return False
        log.info("worker claimed job %s (%s, attempt %d/%d)", job.job_id, job.kind, job.attempts, job.max_attempts)
        self.service.execute_async(job)
        self.processed += 1
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self.last_loop = time.monotonic()
            try:
                worked = self.service.async_enabled and self.run_once()
            except Exception:  # job store down: back off, never die
                log.exception("async worker loop error; retrying")
                worked = False
            if not worked:
                self.service.wake.wait(self.settings.worker_poll_seconds)
                self.service.wake.clear()
