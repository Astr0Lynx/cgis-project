# Operation orchestration: validate -> catalog lookup -> job record -> raster work -> result.
# "mode": "sync" (default) runs inside the request with the candidate window in memory (limit
# MAX_WINDOW_PIXELS); "async" (or "auto" for large AOIs) records an async job and answers 202, and a
# background worker (worker.py) runs it with the bounded-memory blockwise code, heartbeat, progress,
# cancellation, a time limit and bounded retries. Bad input, unknown or not-ready datasets and BUSY
# are rejected before a job exists; once accepted, any failure marks the job failed, removes partial
# outputs and (sync) adds details.job_id to the error.

from __future__ import annotations

import logging
import os
import platform
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from pyproj import CRS
from shapely.geometry import mapping

from . import __version__
from .backends import (
    ASYNC,
    CANCELLED_ERROR,
    COMPLETED,
    FAILED,
    QUEUED,
    RUNNING,
    DatasetRecord,
    InvalidTransition,
    Job,
    validate_dataset_id,
)
from .blockwise import clip_blockwise, window_pixels, zonal_blockwise
from .config import ApiError, Settings
from .files import RESULT_FILE, OutputManager, resolve_asset_path, safe_filename_component
from .geometry import crs_label, parse_aoi, parse_crs, transform_geometry
from .raster import (
    PIXEL_SELECTION,
    Deadline,
    classify_units,
    gdal_env,
    monotonic_deadline,
    open_raster,
    select_pixels,
    write_clip,
    zonal_statistics,
)

log = logging.getLogger(__name__)

ALL_OPERATIONS = ("sum", "mean", "count")
FORMATS = {"gtiff": "GTiff", "geotiff": "GTiff", "cog": "COG"}
MEDIA_TYPES = {
    "GTiff": "image/tiff; application=geotiff",
    "COG": "image/tiff; application=geotiff; profile=cloud-optimized",
}

# A job body: (job_id, job_dir, step) -> (result dict, primary output file). `step` is the sync
# Deadline (step(stage)) or, for async jobs, also a progress callback (step(fraction, message)).
Work = Callable[[uuid.UUID, Path, Callable[..., None]], tuple[dict[str, Any], Path]]
# (HTTP status, body): 200 for a synchronous result, 202 for a queued async job.
Accepted = tuple[int, dict[str, Any]]


# Raised at an async job's checkpoint after a cancel request. Used by execute_async.
class _Cancelled(Exception):
    pass


# Current UTC time as ISO 8601. Used for computed_at/completed_at.
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Normalise the requested zonal operations (default all; "count_valid" -> "count"; deduplicated);
# 422 UNSUPPORTED_OPERATION otherwise. Used by ProcessingService.zonal_statistics.
def normalize_operations(requested: list[str] | None) -> list[str]:
    if requested is None:
        return list(ALL_OPERATIONS)
    if not requested:
        raise ApiError(422, "UNSUPPORTED_OPERATION", "operations must not be empty")
    ops: list[str] = []
    for op in requested:
        name = op.strip().lower() if isinstance(op, str) else op
        if name == "count_valid":
            name = "count"
        if name not in ALL_OPERATIONS:
            raise ApiError(
                422,
                "UNSUPPORTED_OPERATION",
                f"unsupported operation {str(op)[:32]!r}; supported: {', '.join(ALL_OPERATIONS)}",
            )
        if name not in ops:
            ops.append(name)
    return ops


# Map a requested export format to "GTiff" or "COG" (case-insensitive); 422 UNSUPPORTED_FORMAT
# otherwise. Used by ProcessingService.clip.
def normalize_format(fmt: str) -> str:
    normalized = FORMATS.get(fmt.strip().lower()) if isinstance(fmt, str) else None
    if normalized is None:
        raise ApiError(422, "UNSUPPORTED_FORMAT", f"unsupported export format {str(fmt)[:32]!r}; supported: GTiff, COG")
    return normalized


# The processing service behind the HTTP routes; holds settings, backends and the concurrency
# limit. Created by app.create_app.
class ProcessingService:
    # Wire settings, catalog, job store and output manager; set up the job slots.
    def __init__(self, settings: Settings, catalog, jobs, outputs: OutputManager):
        self.settings = settings
        self.catalog = catalog
        self.jobs = jobs
        self.outputs = outputs
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_jobs)
        self._active: set[uuid.UUID] = set()
        self._active_lock = threading.Lock()
        self.wake = threading.Event()  # set when an async job is queued (wakes idle workers)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    # Public URL prefix for generated links (e.g. "/api"). Used by clip and job_view.
    @property
    def _prefix(self) -> str:
        return self.settings.root_path.rstrip("/")

    # Validate the fields shared by both operations and look up the dataset.
    # Used by zonal_statistics and clip.
    def _validate(self, req) -> tuple[DatasetRecord, CRS, Any]:
        validate_dataset_id(req.dataset_id)
        aoi_crs = parse_crs(req.aoi_crs, "aoi_crs")
        geom = parse_aoi(req.aoi, aoi_crs, self.settings)
        return self.catalog.get_ready(req.dataset_id), aoi_crs, geom

    # Open the dataset's raster; yields (src, raster CRS, AOI in the raster CRS) while the file is
    # open. Used by _window_pixels and the work functions.
    @contextmanager
    def _open(self, record: DatasetRecord, aoi_crs: CRS, geom):
        path = resolve_asset_path(self.settings.asset_root, record.asset_path)
        with gdal_env(self.settings.gdal_cachemax_mb), open_raster(path) as src:
            raster_crs = CRS.from_user_input(src.crs)
            yield src, raster_crs, transform_geometry(geom, aoi_crs, raster_crs)

    # In-memory pixel selection within the sync window limit. Used by the work functions (sync path).
    def _select(self, src, native, record: DatasetRecord, deadline: Deadline):
        return select_pixels(
            src,
            native,
            max_window_pixels=self.settings.max_window_pixels,
            catalog_nodata=record.nodata,
            deadline=deadline,
        )

    # ------------------------------------------------------------------ async routing
    # Whether background jobs can run: workers configured and a job store with async support.
    # Used by _route, _sync_hint, worker.WorkerPool and app.ready.
    @property
    def async_enabled(self) -> bool:
        return self.settings.async_workers > 0 and bool(getattr(self.jobs, "supports_async", False))

    # Candidate-window size of the AOI on the dataset's raster. Used by _route.
    def _window_pixels(self, record: DatasetRecord, aoi_crs: CRS, geom) -> int:
        with self._open(record, aoi_crs, geom) as (src, _, native):
            return window_pixels(src, native)

    # Decide how to run a request: None -> synchronously; otherwise the candidate-window size of a
    # job to queue. 413 over the async limit, 503 ASYNC_UNAVAILABLE without workers.
    # Used by zonal_statistics and clip.
    def _route(self, mode: str, record: DatasetRecord, aoi_crs: CRS, geom) -> int | None:
        if mode == "sync":
            return None
        pixels = self._window_pixels(record, aoi_crs, geom)
        if mode == "auto" and pixels <= self.settings.max_window_pixels:
            return None
        if pixels > self.settings.async_max_window_pixels:
            raise ApiError(
                413,
                "AOI_TOO_LARGE",
                f"the AOI covers {pixels} raster pixels, more than this server processes "
                f"({self.settings.async_max_window_pixels} even as a background job)",
                {"pixels": pixels, "limit": self.settings.max_window_pixels,
                 "async_limit": self.settings.async_max_window_pixels},
            )
        if not self.async_enabled:
            raise ApiError(503, "ASYNC_UNAVAILABLE",
                           "background jobs are not enabled on this server (ASYNC_WORKERS=0 or the job store "
                           "lacks database migration core/0009)")
        return pixels

    # Record a queued async job and wake a worker; returns 202 with the job. Used by zonal_statistics and clip.
    def _enqueue(self, kind: str, job_input: dict[str, Any], pixels: int) -> Accepted:
        try:
            job = self.jobs.create(kind, {**job_input, "candidate_window_pixels": pixels}, execution=ASYNC,
                                   max_attempts=self.settings.async_max_attempts)
        except Exception as exc:
            log.exception("job store unavailable")
            raise ApiError(503, "JOB_STORE_UNAVAILABLE", "job store is unavailable") from exc
        self.wake.set()
        log.info("job %s kind=%s dataset=%s queued (async, %d candidate pixels)",
                 job.job_id, kind, job_input.get("dataset_id"), pixels)
        return 202, self.job_view(job)

    # Run a request synchronously; an AOI_TOO_LARGE error gets a hint that async mode can process it.
    # Used by zonal_statistics and clip.
    def _run_sync(self, kind: str, job_input: dict[str, Any], work: Work) -> tuple[Job, dict[str, Any]]:
        try:
            return self._run(kind, job_input, work)
        except ApiError as exc:
            pixels = exc.details.get("pixels") or 0
            if exc.code == "AOI_TOO_LARGE" and self.async_enabled and pixels <= self.settings.async_max_window_pixels:
                exc.details = {**exc.details, "async_limit": self.settings.async_max_window_pixels,
                               "hint": 'resubmit with "mode": "async" (or "auto") to run it as a background job'}
            raise

    # ------------------------------------------------------------------ sync execution
    # Run `work` as a job: take a concurrency slot (503 BUSY if none), create the job, run it in a
    # fresh output directory, store result.json and mark it completed, or mark it failed and clean up.
    # Used by _run_sync.
    def _run(self, kind: str, job_input: dict[str, Any], work: Work) -> tuple[Job, dict[str, Any]]:
        if not self._slots.acquire(timeout=self.settings.queue_wait_seconds):
            raise ApiError(
                503,
                "BUSY",
                "the processing service is at its concurrency limit; retry shortly",
                {"retry_after_seconds": 5},
            )
        started = time.monotonic()
        job_id: uuid.UUID | None = None
        try:
            with self._active_lock:
                active = set(self._active)
            self.outputs.maybe_cleanup(self.settings.output_ttl_seconds, self.settings.cleanup_interval_seconds, active)
            try:
                job = self.jobs.create(kind, job_input)
            except Exception as exc:
                log.exception("job store unavailable")
                raise ApiError(503, "JOB_STORE_UNAVAILABLE", "job store is unavailable") from exc
            job_id = job.job_id
            with self._active_lock:
                self._active.add(job_id)
            try:
                job_dir = self.outputs.create_job_dir(job_id)
                self.jobs.transition(job_id, RUNNING)
                result, primary = work(job_id, job_dir, monotonic_deadline(self.settings.max_job_seconds))
                self.outputs.write_json(job_dir, RESULT_FILE, result)
                job = self.jobs.transition(job_id, COMPLETED, result_path=self.outputs.relative(primary))
            except ApiError as exc:
                self._fail(job_id, {"code": exc.code, "message": exc.message})
                exc.details = {**exc.details, "job_id": str(job_id)}
                raise
            except Exception as exc:
                log.exception("job %s failed unexpectedly", job_id)
                self._fail(job_id, {"code": "INTERNAL_ERROR", "message": "internal processing error"})
                raise ApiError(500, "INTERNAL_ERROR", "internal processing error", {"job_id": str(job_id)}) from exc
            log.info(
                "job %s kind=%s dataset=%s completed in %.3fs",
                job_id, kind, job_input.get("dataset_id"), time.monotonic() - started,
            )
            return job, result
        finally:
            if job_id is not None:
                with self._active_lock:
                    self._active.discard(job_id)
            self._slots.release()

    # Mark a job failed with `error` and remove its partial outputs (best effort). Used by _run and execute_async.
    def _fail(self, job_id: uuid.UUID, error: dict[str, Any]) -> None:
        try:
            self.jobs.transition(job_id, FAILED, error=error)
        except Exception:
            log.exception("could not record failure for job %s", job_id)
        job_dir = self.outputs.job_dir(job_id)
        if job_dir.is_dir():
            try:
                self.outputs.remove_job_dir(job_dir)
            except OSError:
                log.exception("could not remove partial outputs for job %s", job_id)

    # ------------------------------------------------------------------ operations
    # POST /operations/zonal-statistics: pixel-center sum/mean/count over the AOI; (200, flat statistics
    # response) or (202, queued job). Used by app.zonal_statistics.
    def zonal_statistics(self, req) -> Accepted:
        operations = normalize_operations(req.operations)
        record, aoi_crs, geom = self._validate(req)
        if classify_units(record.units) == "density" and req.operations is not None and "sum" in operations:
            raise ApiError(
                422,
                "UNIT_SEMANTICS",
                f"dataset units '{record.units}' are a density; summing density cells does not give a count. "
                "Request 'mean'/'count' instead (area-weighted totals are not provided).",
                {"units": record.units},
            )
        job_input = {
            "dataset_id": record.dataset_id,
            "dataset_version": record.version,
            "aoi": req.aoi,
            "aoi_crs": crs_label(aoi_crs),
            "operations": operations,
        }
        pixels = self._route(req.mode, record, aoi_crs, geom)
        if pixels is not None:
            return self._enqueue("zonal-statistics", job_input, pixels)
        job, result = self._run_sync("zonal-statistics", job_input, self._zonal_work(record, aoi_crs, geom, operations))
        return 200, {"job_id": str(job.job_id), "status": job.status, **result}

    # Build the zonal-statistics job body (in memory, or blockwise for async jobs); the result is
    # stored as result.json. Used by zonal_statistics and _async_work.
    def _zonal_work(self, record: DatasetRecord, aoi_crs: CRS, geom, operations: list[str], *,
                    blockwise: bool = False) -> Work:
        # Compute the statistics. Run by _run or execute_async.
        def work(job_id: uuid.UUID, job_dir: Path, step) -> tuple[dict[str, Any], Path]:
            with self._open(record, aoi_crs, geom) as (src, raster_crs, native):
                if blockwise:
                    stats = zonal_blockwise(src, native, units=record.units, operations=operations,
                                            catalog_nodata=record.nodata, block=self.settings.block_size,
                                            progress=step)
                    nodata = src.nodata if src.nodata is not None else record.nodata
                else:
                    sel = self._select(src, native, record, step)
                    stats = zonal_statistics(sel, record.units, operations)
                    nodata = sel.nodata
            result = {
                "dataset_id": record.dataset_id,
                "dataset_version": record.version,
                **stats,
                "units": record.units,
                "nodata_value": nodata,
                "aoi_crs": crs_label(aoi_crs),
                "raster_crs": crs_label(raster_crs),
                "pixel_selection": PIXEL_SELECTION,
                "operations": operations,
                "dataset": record.public_metadata(),
                "computed_at": _now(),
            }
            return result, job_dir / RESULT_FILE

        return work

    # POST /operations/clip: export the AOI's pixels as GeoTIFF/COG plus a JSON metadata sidecar;
    # (200, completed job) or (202, queued job). Used by app.clip.
    def clip(self, req) -> Accepted:
        fmt = normalize_format(req.format)
        output_crs = parse_crs(req.output_crs, "output_crs") if req.output_crs is not None else None
        record, aoi_crs, geom = self._validate(req)
        job_input = {
            "dataset_id": record.dataset_id,
            "dataset_version": record.version,
            "aoi": req.aoi,
            "aoi_crs": crs_label(aoi_crs),
            "output_crs": crs_label(output_crs) if output_crs else None,
            "format": fmt,
        }
        pixels = self._route(req.mode, record, aoi_crs, geom)
        if pixels is not None:
            return self._enqueue("clip", job_input, pixels)
        job, _ = self._run_sync("clip", job_input, self._clip_work(record, aoi_crs, geom, output_crs, fmt))
        return 200, self.job_view(job)

    # Build the clip job body (in memory, or blockwise for async jobs). Used by clip and _async_work.
    def _clip_work(self, record: DatasetRecord, aoi_crs: CRS, geom, output_crs: CRS | None, fmt: str, *,
                   blockwise: bool = False) -> Work:
        # Write the clip and its sidecar into job_dir; the clip file is the job's result_path.
        # Run by _run or execute_async.
        def work(job_id: uuid.UUID, job_dir: Path, step) -> tuple[dict[str, Any], Path]:
            stem = f"clip_{safe_filename_component(record.dataset_id)}_v{record.version}_{job_id.hex[:12]}"
            out_path = job_dir / f"{stem}.tif"
            tags = {
                "DATASET_ID": record.dataset_id,
                "DATASET_VERSION": str(record.version),
                "UNITS": record.units or "",
                "PIXEL_SELECTION": "pixel_center (all_touched=false)",
                "SOURCE": record.source or "",
                "PROCESSING": f"cgis-spatial-processing {__version__} clip job {job_id}",
            }
            with self._open(record, aoi_crs, geom) as (src, raster_crs, native):
                if blockwise:
                    done = clip_blockwise(src, native, out_path, fmt=fmt, output_crs=output_crs, tags=tags,
                                          units=record.units, catalog_nodata=record.nodata,
                                          block=self.settings.block_size,
                                          max_output_bytes=self.settings.async_max_output_bytes, progress=step)
                    output, count_valid, pixels_in_aoi = done["output"], done["count_valid"], done["pixels_in_aoi"]
                else:
                    sel = self._select(src, native, record, step)
                    if sel.values.nbytes > self.settings.max_output_bytes:
                        limit = self.settings.max_output_bytes
                        raise ApiError(413, "OUTPUT_TOO_LARGE", f"the clipped output would exceed {limit} bytes")
                    output = write_clip(
                        src, sel, out_path, fmt=fmt, output_crs=output_crs, tags=tags, units=record.units, deadline=step
                    )
                    count_valid = int(sel.valid.sum())
                    pixels_in_aoi = int(sel.inside.sum())
            result = {
                "dataset_id": record.dataset_id,
                "dataset_version": record.version,
                "units": record.units,
                "aoi_crs": crs_label(aoi_crs),
                "raster_crs": crs_label(raster_crs),
                "pixel_selection": PIXEL_SELECTION,
                "pixels_in_aoi": pixels_in_aoi,
                "count_valid": count_valid,
                "nodata_excluded": pixels_in_aoi - count_valid,
                "filename": out_path.name,
                "media_type": MEDIA_TYPES[fmt],
                "output": output,
                "warnings": output.pop("warnings"),
                "download_url": f"{self._prefix}/downloads/{job_id}",
                "metadata_url": f"{self._prefix}/downloads/{job_id}/metadata",
                "completed_at": _now(),
            }
            if blockwise:
                result["processing"] = {"path": "blockwise", "block_size": self.settings.block_size}
            sidecar = {
                **result,
                "job_id": str(job_id),
                "aoi": mapping(geom),
                "dataset": record.public_metadata(),
                "software": {
                    "cgis-spatial-processing": __version__,
                    "rasterio": rasterio.__version__,
                    "gdal": rasterio.__gdal_version__,
                    "numpy": np.__version__,
                    "python": platform.python_version(),
                },
            }
            self.outputs.write_json(job_dir, f"{stem}.metadata.json", sidecar)
            return result, out_path

        return work

    # ------------------------------------------------------------------ async execution (worker side)
    # Run one claimed async job to a terminal state with a heartbeat thread, progress, cancellation
    # checkpoints and the ASYNC_MAX_JOB_SECONDS limit; never raises (errors are recorded on the job).
    # Used by worker.WorkerPool.run_once and tests.
    def execute_async(self, job: Job) -> Job:
        settings = self.settings
        state = {"fraction": 0.0, "message": "started"}
        cancel = threading.Event()
        stop_beat = threading.Event()
        end = time.monotonic() + settings.async_max_job_seconds

        # Every worker_heartbeat_seconds: report progress and learn about cancel requests.
        def beat() -> None:
            while not stop_beat.wait(settings.worker_heartbeat_seconds):
                try:
                    if self.jobs.heartbeat(job.job_id, state["fraction"], state["message"]):
                        cancel.set()
                except Exception:  # transient DB error: the next beat retries
                    log.warning("heartbeat failed for job %s", job.job_id)

        # Checkpoint passed to the work: records progress (step(fraction, message)) or accepts the sync
        # deadline style step("stage"); raises on cancellation or timeout.
        def step(fraction=None, message: str = "") -> None:
            if isinstance(fraction, (int, float)):
                state["fraction"] = max(0.0, min(1.0, float(fraction)))
                state["message"] = message or state["message"]
            if cancel.is_set():
                raise _Cancelled()
            if time.monotonic() > end:
                raise ApiError(504, "JOB_TIMEOUT",
                               f"the background job exceeded its {settings.async_max_job_seconds:g}s time limit")

        beater = threading.Thread(target=beat, name=f"heartbeat-{job.job_id}", daemon=True)
        with self._active_lock:
            self._active.add(job.job_id)
        beater.start()
        try:
            if job.cancel_requested or self.jobs.heartbeat(job.job_id, 0.0, "started"):
                raise _Cancelled()
            work = self._async_work(job)
            job_dir = self.outputs.job_dir(job.job_id)
            if job_dir.is_dir():  # left by an earlier attempt that died
                self.outputs.remove_job_dir(job_dir)
            job_dir = self.outputs.create_job_dir(job.job_id)
            result, primary = work(job.job_id, job_dir, step)
            step(1.0, "done")
            self.outputs.write_json(job_dir, RESULT_FILE, result)
            done = self.jobs.transition(job.job_id, COMPLETED, result_path=self.outputs.relative(primary))
            log.info("async job %s kind=%s completed (attempt %d)", job.job_id, job.kind, job.attempts)
            return done
        except _Cancelled:
            self._fail(job.job_id, dict(CANCELLED_ERROR))
            log.info("async job %s cancelled", job.job_id)
        except ApiError as exc:
            self._fail(job.job_id, {"code": exc.code, "message": exc.message})
            log.info("async job %s failed: %s", job.job_id, exc.code)
        except Exception:
            log.exception("async job %s failed unexpectedly", job.job_id)
            self._fail(job.job_id, {"code": "INTERNAL_ERROR", "message": "internal processing error"})
        finally:
            stop_beat.set()
            beater.join(timeout=5)
            with self._active_lock:
                self._active.discard(job.job_id)
        return self.jobs.get(job.job_id) or job

    # Rebuild the work of a queued job from its recorded input (re-validated, never trusted); 409
    # DATASET_VERSION_CHANGED if the dataset moved to another version meanwhile. Used by execute_async.
    def _async_work(self, job: Job) -> Work:
        inp = job.input
        record = self.catalog.get_ready(inp["dataset_id"])
        if record.version != inp.get("dataset_version"):
            raise ApiError(409, "DATASET_VERSION_CHANGED",
                           f"dataset '{record.dataset_id}' moved from version {inp.get('dataset_version')} to "
                           f"{record.version} while the job was queued; resubmit it")
        aoi_crs = parse_crs(inp["aoi_crs"], "aoi_crs")
        geom = parse_aoi(inp["aoi"], aoi_crs, self.settings)
        if job.kind == "zonal-statistics":
            return self._zonal_work(record, aoi_crs, geom, normalize_operations(inp.get("operations")),
                                    blockwise=True)
        if job.kind == "clip":
            output_crs = parse_crs(inp["output_crs"], "output_crs") if inp.get("output_crs") else None
            return self._clip_work(record, aoi_crs, geom, output_crs, normalize_format(inp["format"]),
                                   blockwise=True)
        raise ApiError(422, "UNSUPPORTED_OPERATION", f"unknown job kind {job.kind!r}")

    # POST /operations/{job_id}/cancel: fail a queued async job at once, or flag a running one so its
    # worker stops at the next checkpoint; 409 JOB_NOT_CANCELLABLE for sync or finished jobs.
    # Used by app.cancel_job.
    def cancel_job(self, job_id: uuid.UUID) -> dict[str, Any]:
        job = self._get(job_id)
        if job.execution != ASYNC:
            raise ApiError(409, "JOB_NOT_CANCELLABLE",
                           f"job {job_id} ran synchronously inside its request and cannot be cancelled")
        if job.status in (COMPLETED, FAILED):
            raise ApiError(409, "JOB_NOT_CANCELLABLE", f"job {job_id} is already {job.status}",
                           {"status": job.status})
        job = self.jobs.request_cancel(job_id) or job
        if job.status == QUEUED:
            try:
                job = self.jobs.transition(job_id, FAILED, error=dict(CANCELLED_ERROR))
            except InvalidTransition:
                job = self.jobs.get(job_id) or job  # a worker claimed it meanwhile: it stops at a checkpoint
        return self.job_view(job)

    # ------------------------------------------------------------------ job access
    # Fetch a job or raise 404 JOB_NOT_FOUND. Used by get_job, cancel_job and _completed_clip.
    def _get(self, job_id: uuid.UUID) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", f"job {job_id} was not found")
        return job

    # The public job representation (status, input, result from result.json, error, links, async
    # progress). Used by clip, _enqueue, cancel_job and get_job.
    def job_view(self, job: Job) -> dict[str, Any]:
        links: dict[str, Any] = {"self": f"{self._prefix}/operations/{job.job_id}"}
        if job.kind == "clip" and job.status == COMPLETED:
            links["download"] = f"{self._prefix}/downloads/{job.job_id}"
            links["metadata"] = f"{self._prefix}/downloads/{job.job_id}/metadata"
        if job.execution == ASYNC and job.status in (QUEUED, RUNNING):
            links["cancel"] = f"{self._prefix}/operations/{job.job_id}/cancel"
        result = None
        if job.status == COMPLETED:
            result = self.outputs.read_result(job.job_id) or {"expired": True}
        return {
            "job_id": str(job.job_id),
            "kind": job.kind,
            "status": job.status,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "input": job.input,
            "result": result,
            "error": job.error,
            "links": links,
            "execution": job.execution,
            "progress": job.progress,
            "progress_message": job.progress_message,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "cancel_requested": job.cancel_requested,
        }

    # GET /operations/{job_id}. Used by app.get_job.
    def get_job(self, job_id: uuid.UUID) -> dict[str, Any]:
        return self.job_view(self._get(job_id))

    # Return a completed clip job and its output file, or raise 404/409/410.
    # Used by download and download_metadata.
    def _completed_clip(self, job_id: uuid.UUID) -> tuple[Job, Path]:
        job = self._get(job_id)
        if job.kind != "clip":
            raise ApiError(409, "NO_DOWNLOAD", f"job {job_id} ({job.kind}) has no downloadable file")
        if job.status == FAILED:
            raise ApiError(409, "JOB_FAILED", f"job {job_id} failed; nothing to download", {"error": job.error})
        if job.status != COMPLETED:
            raise ApiError(409, "JOB_NOT_COMPLETED", f"job {job_id} is {job.status}; retry when completed")
        return job, self.outputs.resolve_result(job.result_path)

    # GET /downloads/{job_id}: (file path, media type) of the clip. Used by app.download.
    def download(self, job_id: uuid.UUID) -> tuple[Path, str]:
        job, path = self._completed_clip(job_id)
        return path, MEDIA_TYPES.get(job.input.get("format", "GTiff"), MEDIA_TYPES["GTiff"])

    # GET /downloads/{job_id}/metadata: path of the clip's JSON sidecar. Used by app.download_metadata.
    def download_metadata(self, job_id: uuid.UUID) -> Path:
        _, path = self._completed_clip(job_id)
        return self.outputs.resolve_result(self.outputs.relative(path.with_name(path.stem + ".metadata.json")))

    # GET /ready: check the asset root, a writable output root, catalog and job store.
    # Used by app.ready.
    def readiness(self) -> dict[str, Any]:
        checks: dict[str, str] = {"asset_root": "ok" if self.settings.asset_root.is_dir() else "missing"}
        try:
            self.outputs.ensure_root()
            probe = self.outputs.root / f".tmp-ready-{uuid.uuid4().hex}"
            probe.write_bytes(b"")
            probe.unlink()
            checks["output_root"] = "ok"
        except OSError:
            checks["output_root"] = "not_writable"
        for name, backend in (("catalog", self.catalog), ("job_store", self.jobs)):
            try:
                backend.ping()
                checks[name] = "ok"
            except Exception:
                checks[name] = "unavailable"
        return {"ready": all(v == "ok" for v in checks.values()), "checks": checks}
