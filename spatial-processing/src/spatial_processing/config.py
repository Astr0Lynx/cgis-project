# Runtime settings (read once from environment variables) and ApiError, the one exception
# type that every module raises for client-facing failures.

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# An error with an HTTP status and a stable machine-readable code; rendered as
# {"error": {"code", "message", "details"?}}. Raised everywhere, rendered by app.py.
# Messages never contain server paths, SQL, stack traces or credentials.
class ApiError(Exception):
    # Store the status, code, message and optional details.
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    # Build the JSON error body. Used by app._error_response.
    def to_body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"error": err}


# Read env var `name`, cast to the type of `default`; unset or empty gives the default and
# negative integers are rejected. Used by Settings.from_env.
def _env(name: str, default: Any) -> Any:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    value = type(default)(raw)
    if isinstance(value, int) and value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


@dataclass(frozen=True)
class Settings:
    # Storage roots. Catalog asset paths are resolved strictly inside asset_root.
    asset_root: Path = Path("/data/assets")
    output_root: Path = Path("/data/outputs")

    # Backends: "postgres" (production), "memory" or "static" (JSON file; development and tests).
    catalog_backend: str = "postgres"
    static_catalog_file: Path | None = None
    job_store_backend: str = "postgres"
    database_url: str | None = None  # libpq conninfo; falls back to the standard PG* env vars

    # Public prefix the gateway exposes the service under (e.g. "/api").
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

    # Large AOIs. A candidate window over max_window_pixels is not loaded into memory: with
    # "mode": "async" (or "auto") it runs block by block in a background worker within these limits.
    # Memory per block ~ block_size^2 x (itemsize + 2) bytes.
    block_size: int = 2048
    async_max_window_pixels: int = 2_000_000_000  # > one whole India 3 arc-second raster (1.21e9)
    async_max_output_bytes: int = 2 * 1024**3
    async_workers: int = 1
    async_max_job_seconds: float = 3600.0
    async_max_attempts: int = 2
    async_queue_ttl_seconds: int = 6 * 3600
    worker_poll_seconds: float = 2.0
    worker_heartbeat_seconds: float = 5.0
    worker_stale_seconds: float = 60.0

    # Build Settings from the environment (each field's env var is its upper-case name).
    # Used by app.create_app when no Settings are passed.
    @classmethod
    def from_env(cls) -> "Settings":
        static_file = os.environ.get("STATIC_CATALOG_FILE")
        return cls(
            asset_root=_env("ASSET_ROOT", cls.asset_root),
            output_root=_env("OUTPUT_ROOT", cls.output_root),
            catalog_backend=_env("CATALOG_BACKEND", cls.catalog_backend),
            static_catalog_file=Path(static_file) if static_file else None,
            job_store_backend=_env("JOB_STORE_BACKEND", cls.job_store_backend),
            database_url=os.environ.get("DATABASE_URL") or None,
            root_path=_env("ROOT_PATH", cls.root_path),
            max_request_bytes=_env("MAX_REQUEST_BYTES", cls.max_request_bytes),
            max_aoi_vertices=_env("MAX_AOI_VERTICES", cls.max_aoi_vertices),
            max_aoi_parts=_env("MAX_AOI_PARTS", cls.max_aoi_parts),
            max_window_pixels=_env("MAX_WINDOW_PIXELS", cls.max_window_pixels),
            max_output_bytes=_env("MAX_OUTPUT_BYTES", cls.max_output_bytes),
            max_concurrent_jobs=max(1, _env("MAX_CONCURRENT_JOBS", cls.max_concurrent_jobs)),
            queue_wait_seconds=_env("QUEUE_WAIT_SECONDS", cls.queue_wait_seconds),
            max_job_seconds=_env("MAX_JOB_SECONDS", cls.max_job_seconds),
            output_ttl_seconds=_env("OUTPUT_TTL_SECONDS", cls.output_ttl_seconds),
            cleanup_interval_seconds=_env("CLEANUP_INTERVAL_SECONDS", cls.cleanup_interval_seconds),
            gdal_cachemax_mb=_env("GDAL_CACHEMAX_MB", cls.gdal_cachemax_mb),
            block_size=max(256, _env("BLOCK_SIZE", cls.block_size)),
            async_max_window_pixels=_env("ASYNC_MAX_WINDOW_PIXELS", cls.async_max_window_pixels),
            async_max_output_bytes=_env("ASYNC_MAX_OUTPUT_BYTES", cls.async_max_output_bytes),
            async_workers=_env("ASYNC_WORKERS", cls.async_workers),
            async_max_job_seconds=_env("ASYNC_MAX_JOB_SECONDS", cls.async_max_job_seconds),
            async_max_attempts=min(10, max(1, _env("ASYNC_MAX_ATTEMPTS", cls.async_max_attempts))),
            async_queue_ttl_seconds=_env("ASYNC_QUEUE_TTL_SECONDS", cls.async_queue_ttl_seconds),
            worker_poll_seconds=_env("WORKER_POLL_SECONDS", cls.worker_poll_seconds),
            worker_heartbeat_seconds=_env("WORKER_HEARTBEAT_SECONDS", cls.worker_heartbeat_seconds),
            worker_stale_seconds=_env("WORKER_STALE_SECONDS", cls.worker_stale_seconds),
        )
