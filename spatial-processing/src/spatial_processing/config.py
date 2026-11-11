"""Runtime configuration, read once from environment variables.

Every value has a safe default so the service can start for local development;
in Compose the values are injected by the stack (see docs/compose-request.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    # Storage roots. Asset paths from the catalog are resolved strictly inside asset_root.
    asset_root: Path = Path("/data/assets")
    output_root: Path = Path("/data/outputs")

    # Backends: "postgres" (production) or "memory"/"static" (development, tests).
    catalog_backend: str = "postgres"
    static_catalog_file: Path | None = None
    job_store_backend: str = "postgres"
    database_url: str | None = None  # libpq conninfo; falls back to standard PG* env vars

    # Prefix under which the gateway exposes the service (prefix is stripped by the gateway).
    root_path: str = ""

    # Guardrails.
    max_request_bytes: int = 1_000_000
    max_aoi_vertices: int = 10_000
    max_aoi_parts: int = 100
    max_window_pixels: int = 25_000_000  # pixels read for one operation (memory bound)
    max_output_bytes: int = 512 * 1024 * 1024
    max_concurrent_jobs: int = 4
    queue_wait_seconds: float = 10.0
    max_job_seconds: float = 120.0
    output_ttl_seconds: int = 24 * 3600
    cleanup_interval_seconds: int = 600
    gdal_cachemax_mb: int = 256

    # Large AOIs (completion P2/P3). AOIs whose candidate window exceeds max_window_pixels are not
    # loaded into memory: with "mode": "async" (or "auto") they run block by block in a background
    # worker, bounded by these limits. Memory per block ~ block_size^2 x (itemsize + 2) bytes.
    block_size: int = 2048
    async_max_window_pixels: int = 2_000_000_000   # > one whole India 3 arc-second raster (1.21e9)
    async_max_output_bytes: int = 2 * 1024**3
    async_workers: int = 1
    async_max_job_seconds: float = 3600.0
    async_max_attempts: int = 2
    async_queue_ttl_seconds: int = 6 * 3600
    worker_poll_seconds: float = 2.0
    worker_heartbeat_seconds: float = 5.0
    worker_stale_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        static_file = os.environ.get("STATIC_CATALOG_FILE")
        return cls(
            asset_root=Path(os.environ.get("ASSET_ROOT", "/data/assets")),
            output_root=Path(os.environ.get("OUTPUT_ROOT", "/data/outputs")),
            catalog_backend=os.environ.get("CATALOG_BACKEND", "postgres"),
            static_catalog_file=Path(static_file) if static_file else None,
            job_store_backend=os.environ.get("JOB_STORE_BACKEND", "postgres"),
            database_url=os.environ.get("DATABASE_URL") or None,
            root_path=os.environ.get("ROOT_PATH", ""),
            max_request_bytes=_int("MAX_REQUEST_BYTES", cls.max_request_bytes),
            max_aoi_vertices=_int("MAX_AOI_VERTICES", cls.max_aoi_vertices),
            max_aoi_parts=_int("MAX_AOI_PARTS", cls.max_aoi_parts),
            max_window_pixels=_int("MAX_WINDOW_PIXELS", cls.max_window_pixels),
            max_output_bytes=_int("MAX_OUTPUT_BYTES", cls.max_output_bytes),
            max_concurrent_jobs=max(1, _int("MAX_CONCURRENT_JOBS", cls.max_concurrent_jobs)),
            queue_wait_seconds=_float("QUEUE_WAIT_SECONDS", cls.queue_wait_seconds),
            max_job_seconds=_float("MAX_JOB_SECONDS", cls.max_job_seconds),
            output_ttl_seconds=_int("OUTPUT_TTL_SECONDS", cls.output_ttl_seconds),
            cleanup_interval_seconds=_int("CLEANUP_INTERVAL_SECONDS", cls.cleanup_interval_seconds),
            gdal_cachemax_mb=_int("GDAL_CACHEMAX_MB", cls.gdal_cachemax_mb),
            block_size=max(256, _int("BLOCK_SIZE", cls.block_size)),
            async_max_window_pixels=_int("ASYNC_MAX_WINDOW_PIXELS", cls.async_max_window_pixels),
            async_max_output_bytes=_int("ASYNC_MAX_OUTPUT_BYTES", cls.async_max_output_bytes),
            async_workers=_int("ASYNC_WORKERS", cls.async_workers),
            async_max_job_seconds=_float("ASYNC_MAX_JOB_SECONDS", cls.async_max_job_seconds),
            async_max_attempts=min(10, max(1, _int("ASYNC_MAX_ATTEMPTS", cls.async_max_attempts))),
            async_queue_ttl_seconds=_int("ASYNC_QUEUE_TTL_SECONDS", cls.async_queue_ttl_seconds),
            worker_poll_seconds=_float("WORKER_POLL_SECONDS", cls.worker_poll_seconds),
            worker_heartbeat_seconds=_float("WORKER_HEARTBEAT_SECONDS", cls.worker_heartbeat_seconds),
            worker_stale_seconds=_float("WORKER_STALE_SECONDS", cls.worker_stale_seconds),
        )
