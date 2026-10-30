"""Operation orchestration: validation -> catalog -> job record -> processing -> result.

Execution is synchronous inside the request (acceptable for Phase 2 fixtures), but every
operation is recorded as a job with the same status contract a background worker would
use, so a queue/worker can be introduced later without changing the HTTP API.

Error/job policy:
* Malformed input (bad dataset ID, CRS, AOI, format, operations), unknown/not-ready
  datasets and BUSY rejections are answered before a job exists (no job is created).
* Once accepted, any failure marks the job ``failed`` with its error, removes partial
  outputs, and the error response carries ``details.job_id``.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from pyproj import CRS
from shapely.geometry import mapping

from . import __version__, errors
from .catalog import Catalog, DatasetRecord, validate_dataset_id
from .config import Settings
from .errors import ApiError
from .geometry import crs_label, parse_aoi, parse_crs, transform_geometry
from .jobs import COMPLETED, FAILED, RUNNING, Job, JobStore
from .models import ClipRequest, ZonalStatisticsRequest
from .outputs import RESULT_FILE, OutputManager
from .paths import resolve_asset_path, safe_filename_component
from .raster import (
    PIXEL_SELECTION,
    estimate_bytes,
    gdal_env,
    monotonic_deadline,
    open_raster,
    select_pixels,
    write_clip,
    zonal_statistics,
)
from .units import classify_units

log = logging.getLogger(__name__)

ALL_OPERATIONS = ("sum", "mean", "count")
FORMATS = {"gtiff": "GTiff", "geotiff": "GTiff", "cog": "COG"}
MEDIA_TYPES = {
    "GTiff": "image/tiff; application=geotiff",
    "COG": "image/tiff; application=geotiff; profile=cloud-optimized",
}

Work = Callable[[uuid.UUID, Path, Callable[[str], None]], tuple[dict[str, Any], Path]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcessingService:
    def __init__(self, settings: Settings, catalog: Catalog, jobs: JobStore, outputs: OutputManager):
        self.settings = settings
        self.catalog = catalog
        self.jobs = jobs
        self.outputs = outputs
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_jobs)
        self._active: set[uuid.UUID] = set()
        self._active_lock = threading.Lock()

    # ------------------------------------------------------------------ validation
    def _validate_common(self, req) -> tuple[DatasetRecord, CRS, Any]:
        validate_dataset_id(req.dataset_id)
        aoi_crs = parse_crs(req.aoi_crs, "aoi_crs")
        geom = parse_aoi(req.aoi, aoi_crs, self.settings)
        record = self.catalog.get_ready(req.dataset_id)
        return record, aoi_crs, geom

    @staticmethod
    def _normalize_operations(requested: list[str] | None) -> list[str]:
        if requested is None:
            return list(ALL_OPERATIONS)
        if not requested:
            raise ApiError(422, errors.UNSUPPORTED_OPERATION, "operations must not be empty")
        ops: list[str] = []
        for op in requested:
            name = op.strip().lower() if isinstance(op, str) else op
            if name == "count_valid":
                name = "count"
            if name not in ALL_OPERATIONS:
                raise ApiError(
                    422,
                    errors.UNSUPPORTED_OPERATION,
                    f"unsupported operation {str(op)[:32]!r}; supported: {', '.join(ALL_OPERATIONS)}",
                )
            if name not in ops:
                ops.append(name)
        return ops

    @staticmethod
    def _normalize_format(fmt: str) -> str:
        normalized = FORMATS.get(fmt.strip().lower()) if isinstance(fmt, str) else None
        if normalized is None:
            raise ApiError(
                422,
                errors.UNSUPPORTED_FORMAT,
                f"unsupported export format {str(fmt)[:32]!r}; supported: GTiff, COG",
            )
        return normalized

    # ------------------------------------------------------------------ job runner
    def _run(self, kind: str, job_input: dict[str, Any], work: Work) -> tuple[Job, dict[str, Any]]:
        if not self._slots.acquire(timeout=self.settings.queue_wait_seconds):
            raise ApiError(
                503,
                errors.BUSY,
                "the processing service is at its concurrency limit; retry shortly",
                {"retry_after_seconds": 5},
            )
        started = time.monotonic()
        job_id: uuid.UUID | None = None
        try:
            with self._active_lock:
                active = set(self._active)
            self.outputs.maybe_cleanup(
                self.settings.output_ttl_seconds, self.settings.cleanup_interval_seconds, active
            )
            try:
                job = self.jobs.create(kind, job_input)
            except Exception as exc:
                log.exception("job store unavailable")
                raise ApiError(503, errors.JOB_STORE_UNAVAILABLE, "job store is unavailable") from exc
            job_id = job.job_id
            with self._active_lock:
                self._active.add(job_id)
            try:
                job_dir = self.outputs.create_job_dir(job.job_id)
                self.jobs.transition(job.job_id, RUNNING)
                deadline = monotonic_deadline(self.settings.max_job_seconds)
                result, primary = work(job.job_id, job_dir, deadline)
                self.outputs.write_json(job_dir, RESULT_FILE, result)
                job = self.jobs.transition(job.job_id, COMPLETED, result_path=self.outputs.relative(primary))
            except ApiError as exc:
                self._fail(job, {"code": exc.code, "message": exc.message})
                exc.details = {**exc.details, "job_id": str(job.job_id)}
                raise
            except Exception as exc:
                log.exception("job %s failed unexpectedly", job.job_id)
                self._fail(job, {"code": errors.INTERNAL_ERROR, "message": "internal processing error"})
                raise ApiError(
                    500, errors.INTERNAL_ERROR, "internal processing error", {"job_id": str(job.job_id)}
                ) from exc
            log.info(
                "job %s kind=%s dataset=%s completed in %.3fs",
                job.job_id,
                kind,
                job_input.get("dataset_id"),
                time.monotonic() - started,
            )
            return job, result
        finally:
            if job_id is not None:
                with self._active_lock:
                    self._active.discard(job_id)
            self._slots.release()

    def _fail(self, job: Job, error: dict[str, Any]) -> None:
        try:
            self.jobs.transition(job.job_id, FAILED, error=error)
        except Exception:
            log.exception("could not record failure for job %s", job.job_id)
        job_dir = self.outputs.job_dir(job.job_id)
        if job_dir.is_dir():
            try:
                self.outputs.remove_job_dir(job_dir)
            except OSError:
                log.exception("could not remove partial outputs for job %s", job.job_id)

    # ------------------------------------------------------------------ operations
    def zonal_statistics(self, req: ZonalStatisticsRequest) -> dict[str, Any]:
        operations = self._normalize_operations(req.operations)
        record, aoi_crs, geom = self._validate_common(req)
        semantics = classify_units(record.units)
        if semantics.kind == "density" and req.operations is not None and "sum" in operations:
            raise ApiError(
                422,
                errors.UNIT_SEMANTICS,
                f"dataset units '{record.units}' are a density; summing density cells does not give a count. "
                "Request 'mean'/'count' instead (area-weighted totals are not provided in Phase 2).",
                {"units": record.units},
            )
        job_input = {
            "dataset_id": record.dataset_id,
            "dataset_version": record.version,
            "aoi": req.aoi,
            "aoi_crs": crs_label(aoi_crs),
            "operations": operations,
        }

        def work(job_id: uuid.UUID, job_dir: Path, deadline) -> tuple[dict[str, Any], Path]:
            path = resolve_asset_path(self.settings.asset_root, record.asset_path)
            with gdal_env(self.settings.gdal_cachemax_mb), open_raster(path) as src:
                raster_crs = CRS.from_user_input(src.crs)
                native = transform_geometry(geom, aoi_crs, raster_crs)
                sel = select_pixels(
                    src,
                    native,
                    max_window_pixels=self.settings.max_window_pixels,
                    catalog_nodata=record.nodata,
                    deadline=deadline,
                )
                stats = zonal_statistics(sel, semantics, operations)
            result = {
                "dataset_id": record.dataset_id,
                "dataset_version": record.version,
                **stats,
                "units": record.units,
                "nodata_value": sel.nodata,
                "aoi_crs": crs_label(aoi_crs),
                "raster_crs": crs_label(raster_crs),
                "pixel_selection": PIXEL_SELECTION,
                "operations": operations,
                "dataset": record.public_metadata(),
                "computed_at": _now(),
            }
            return result, job_dir / RESULT_FILE

        job, result = self._run("zonal-statistics", job_input, work)
        return {"job_id": str(job.job_id), "status": job.status, **result}

    def clip(self, req: ClipRequest) -> dict[str, Any]:
        fmt = self._normalize_format(req.format)
        output_crs = parse_crs(req.output_crs, "output_crs") if req.output_crs is not None else None
        record, aoi_crs, geom = self._validate_common(req)
        job_input = {
            "dataset_id": record.dataset_id,
            "dataset_version": record.version,
            "aoi": req.aoi,
            "aoi_crs": crs_label(aoi_crs),
            "output_crs": crs_label(output_crs) if output_crs else None,
            "format": fmt,
        }

        def work(job_id: uuid.UUID, job_dir: Path, deadline) -> tuple[dict[str, Any], Path]:
            path = resolve_asset_path(self.settings.asset_root, record.asset_path)
            with gdal_env(self.settings.gdal_cachemax_mb), open_raster(path) as src:
                raster_crs = CRS.from_user_input(src.crs)
                native = transform_geometry(geom, aoi_crs, raster_crs)
                sel = select_pixels(
                    src,
                    native,
                    max_window_pixels=self.settings.max_window_pixels,
                    catalog_nodata=record.nodata,
                    deadline=deadline,
                )
                if estimate_bytes(sel) > self.settings.max_output_bytes:
                    raise ApiError(
                        413,
                        errors.OUTPUT_TOO_LARGE,
                        f"the clipped output would exceed {self.settings.max_output_bytes} bytes",
                    )
                stem = (
                    f"clip_{safe_filename_component(record.dataset_id)}_v{record.version}_{job_id.hex[:12]}"
                )
                out_path = job_dir / f"{stem}.tif"
                tags = {
                    "DATASET_ID": record.dataset_id,
                    "DATASET_VERSION": str(record.version),
                    "UNITS": record.units or "",
                    "PIXEL_SELECTION": "pixel_center (all_touched=false)",
                    "SOURCE": record.source or "",
                    "PROCESSING": f"cgis-spatial-processing {__version__} clip job {job_id}",
                }
                output = write_clip(
                    src,
                    sel,
                    out_path,
                    fmt=fmt,
                    output_crs=output_crs,
                    tags=tags,
                    units=record.units,
                    deadline=deadline,
                )
                count_valid = int(sel.valid.sum())
                pixels_in_aoi = int(sel.inside.sum())
            prefix = self.settings.root_path.rstrip("/")
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
                "download_url": f"{prefix}/downloads/{job_id}",
                "metadata_url": f"{prefix}/downloads/{job_id}/metadata",
                "completed_at": _now(),
            }
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

        job, _ = self._run("clip", job_input, work)
        return self.job_view(job)

    # ------------------------------------------------------------------ job access
    def _get(self, job_id: uuid.UUID) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise ApiError(404, errors.JOB_NOT_FOUND, f"job {job_id} was not found")
        return job

    def job_view(self, job: Job) -> dict[str, Any]:
        prefix = self.settings.root_path.rstrip("/")
        links: dict[str, Any] = {"self": f"{prefix}/operations/{job.job_id}"}
        if job.kind == "clip" and job.status == COMPLETED:
            links["download"] = f"{prefix}/downloads/{job.job_id}"
            links["metadata"] = f"{prefix}/downloads/{job.job_id}/metadata"
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
        }

    def get_job(self, job_id: uuid.UUID) -> dict[str, Any]:
        return self.job_view(self._get(job_id))

    def _completed_clip(self, job_id: uuid.UUID) -> tuple[Job, Path]:
        job = self._get(job_id)
        if job.kind != "clip":
            raise ApiError(409, errors.NO_DOWNLOAD, f"job {job_id} ({job.kind}) has no downloadable file")
        if job.status == FAILED:
            raise ApiError(409, errors.JOB_FAILED, f"job {job_id} failed; nothing to download", {"error": job.error})
        if job.status != COMPLETED:
            raise ApiError(409, errors.JOB_NOT_COMPLETED, f"job {job_id} is {job.status}; retry when completed")
        return job, self.outputs.resolve_result(job.result_path)

    def download(self, job_id: uuid.UUID) -> tuple[Path, str, str]:
        job, path = self._completed_clip(job_id)
        fmt = job.input.get("format", "GTiff")
        return path, path.name, MEDIA_TYPES.get(fmt, MEDIA_TYPES["GTiff"])

    def download_metadata(self, job_id: uuid.UUID) -> tuple[Path, str]:
        _, path = self._completed_clip(job_id)
        sidecar = path.with_name(path.stem + ".metadata.json")
        resolved = self.outputs.resolve_result(self.outputs.relative(sidecar))
        return resolved, sidecar.name

    # ------------------------------------------------------------------ health
    def readiness(self) -> dict[str, Any]:
        checks: dict[str, str] = {}
        checks["asset_root"] = "ok" if self.settings.asset_root.is_dir() else "missing"
        try:
            self.outputs.ensure_root()
            probe = self.outputs.root / f".tmp-ready-{uuid.uuid4().hex}"
            probe.write_bytes(b"")
            probe.unlink()
            checks["output_root"] = "ok"
        except OSError:
            checks["output_root"] = "not_writable"
        for name, target in (("catalog", self.catalog), ("job_store", self.jobs)):
            try:
                target.ping()
                checks[name] = "ok"
            except Exception:
                checks[name] = "unavailable"
        return {"ready": all(v == "ok" for v in checks.values()), "checks": checks}
