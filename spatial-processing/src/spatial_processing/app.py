# FastAPI application: request/response schemas, middleware, error handlers and routes.
# Routes are unprefixed (/operations/..., /downloads/...); the gateway exposes them under /api and
# ROOT_PATH=/api makes OpenAPI and generated links use that public prefix.

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .backends import InMemoryCatalog, InMemoryJobStore, PostgresCatalog, PostgresJobStore, load_static_catalog
from .config import ApiError, Settings
from .files import OutputManager
from .service import ProcessingService
from .worker import WorkerPool

log = logging.getLogger("spatial_processing")

# --------------------------------------------------------------------------- schemas
# Requests forbid unknown fields so a client cannot smuggle a file path or URL. CRS, format and
# operation names are validated in the service so they map to specific error codes.

_TOWN_A = {
    "type": "Polygon",
    "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.02, 17.99], [78.01, 17.99], [78.01, 17.98]]],
}


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(..., max_length=128, description="Catalog dataset ID (not a path or URL).")
    aoi: dict[str, Any] = Field(
        ..., description="GeoJSON Polygon or MultiPolygon (or a Feature wrapping one), x/y (lon/lat) order."
    )
    aoi_crs: str = Field(..., max_length=64, description="CRS of `aoi` as 'EPSG:<code>' (e.g. 'EPSG:4326').")
    mode: Literal["sync", "async", "auto"] = Field(
        "sync",
        description="'sync' (default, Phase 2 behaviour): run inside the request; AOIs larger than the "
        "interactive pixel limit get 413 AOI_TOO_LARGE. 'async': queue a background job and return 202 with "
        "its status URL (bounded-memory blockwise processing). 'auto': sync when the AOI is small enough, "
        "otherwise async.",
    )


class ClipRequest(_Request):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "dataset_id": "synthetic-worldpop-2001",
                "aoi": _TOWN_A,
                "aoi_crs": "EPSG:4326",
                "output_crs": "EPSG:4326",
                "format": "GTiff",
            }
        },
    )

    output_crs: str | None = Field(
        None,
        max_length=64,
        description="Output CRS 'EPSG:<code>'. Omit (or use the native CRS) for an exact native-grid clip; "
        "a different CRS triggers nearest-neighbour reprojection (totals not preserved).",
    )
    format: str = Field("GTiff", max_length=16, description="'GTiff' or 'COG'.")


class ZonalStatisticsRequest(_Request):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "dataset_id": "synthetic-worldpop-2001",
                "aoi": _TOWN_A,
                "aoi_crs": "EPSG:4326",
                "operations": ["sum", "mean", "count"],
            }
        },
    )

    operations: list[str] | None = Field(
        None, max_length=8, description="Subset of 'sum', 'mean', 'count'. Default: all."
    )


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class PixelSelection(BaseModel):
    rule: Literal["pixel_center"]
    all_touched: bool
    description: str


class ZonalStatisticsResponse(BaseModel):
    job_id: str
    status: str
    dataset_id: str
    dataset_version: int
    count_valid: int | None = Field(description="Number of non-NoData pixels whose centers fall in the AOI.")
    sum: float | int | None = Field(description="Sum of valid pixel values; see `sum_semantics`.")
    mean: float | None
    units: str | None
    sum_semantics: Literal["total_count", "not_applicable_density", "sum_of_cell_values"]
    pixels_in_aoi: int
    nodata_excluded: int = Field(description="Pixels in the AOI that were NoData/masked and excluded.")
    nodata_value: float | None
    aoi_crs: str
    raster_crs: str
    pixel_selection: PixelSelection
    operations: list[str]
    warnings: list[str]
    dataset: dict[str, Any]


class JobLinks(BaseModel):
    self: str
    download: str | None = None
    metadata: str | None = None
    cancel: str | None = Field(None, description="POST to this URL to cancel a queued/running async job.")


class JobResponse(BaseModel):
    job_id: str
    kind: Literal["clip", "zonal-statistics"]
    status: Literal["queued", "running", "completed", "failed"]
    created_at: datetime
    updated_at: datetime
    input: dict[str, Any]
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None
    links: JobLinks
    execution: Literal["sync", "async"] = "sync"
    progress: float | None = Field(None, description="0..1 while an async job runs; 1 when completed.")
    progress_message: str | None = None
    attempts: int = 0
    max_attempts: int = 1
    cancel_requested: bool = False


# --------------------------------------------------------------------------- app

DESCRIPTION = """
Project-specific processing API (**not** an OGC API standard endpoint).

* Datasets are referenced by catalog `dataset_id` only; raster files are resolved server-side from
  the PostGIS catalog (`catalog.current_datasets`, status `ready`). Paths and URLs are never accepted.
* AOIs are GeoJSON Polygon/MultiPolygon in x/y (lon/lat) order with an explicit `aoi_crs` (`EPSG:<code>`).
* **Pixel selection:** a pixel is included iff its **center** lies inside the AOI (`all_touched=false`).
  NoData pixels are excluded and reported (`nodata_excluded`), never treated as zero.
* Operations run synchronously by default and are recorded as jobs (`queued → running → completed|failed`).
* Large AOIs: send `"mode": "async"` (or `"auto"`) to queue a bounded-memory background job; the
  answer is **202** with the job (poll `GET /operations/{job_id}` for `status`/`progress`, cancel with
  `POST /operations/{job_id}/cancel`). Jobs survive a service restart (re-queued, bounded retries).
* Errors are `{"error": {"code", "message", "details"?}}`.
"""

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (404, 409, 410, 413, 422, 500, 503, 504)
}
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


# Render an ApiError as a JSON response (BUSY adds Retry-After). Used by the error handlers and
# BodySizeLimitMiddleware.
def _error_response(exc: ApiError) -> JSONResponse:
    headers = {}
    if exc.code == "BUSY":
        headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 5))
    return JSONResponse(status_code=exc.status, content=exc.to_body(), headers=headers)


# ASGI middleware: reject POST/PUT/PATCH bodies over `max_bytes` with 413 REQUEST_TOO_LARGE,
# with or without Content-Length. Installed by create_app.
class BodySizeLimitMiddleware:
    # Wrap the app.
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    # Buffer the body up to the limit, then replay it to the app.
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        too_large = _error_response(
            ApiError(413, "REQUEST_TOO_LARGE", f"request body exceeds {self.max_bytes} bytes")
        )
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            return await too_large(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                return await too_large(scope, receive, send)
            if not message.get("more_body", False):
                break
        replayed = False

        # Hand the buffered body to the app once, then pass through later messages.
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


# ASGI middleware: set the public prefix (e.g. /api) as root_path unless the server already did,
# so both /api/operations/... (prefix forwarded) and /operations/... (stripped) route, and OpenAPI
# `servers` shows the prefix. Installed by create_app when ROOT_PATH is set.
class RootPathMiddleware:
    # Wrap the app.
    def __init__(self, app, root_path: str):
        self.app = app
        self.root_path = root_path.rstrip("/")

    # Inject root_path into the scope.
    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not scope.get("root_path"):
            scope = {**scope, "root_path": self.root_path}
        return await self.app(scope, receive, send)


# Set the route's status code (202 adds a Location header to the job) and return the body, so FastAPI
# serializes it like every other route. Used by the clip and zonal_statistics routes.
def _respond(response: Response, status: int, body: dict[str, Any]) -> dict[str, Any]:
    response.status_code = status
    if status == 202:
        response.headers["Location"] = body["links"]["self"]
    return body


# Pick the catalog backend from CATALOG_BACKEND (postgres | static | memory). Used by create_app.
def build_catalog(settings: Settings):
    if settings.catalog_backend == "postgres":
        return PostgresCatalog(settings.database_url)
    if settings.catalog_backend == "static" and settings.static_catalog_file:
        return load_static_catalog(settings.static_catalog_file)
    if settings.catalog_backend == "memory":
        return InMemoryCatalog()
    raise ValueError(f"unsupported CATALOG_BACKEND {settings.catalog_backend!r}")


# Pick the job store from JOB_STORE_BACKEND (postgres | memory). Used by create_app.
def build_job_store(settings: Settings):
    if settings.job_store_backend == "postgres":
        return PostgresJobStore(settings.database_url)
    if settings.job_store_backend == "memory":
        return InMemoryJobStore()
    raise ValueError(f"unsupported JOB_STORE_BACKEND {settings.job_store_backend!r}")


# Build the FastAPI app; `catalog`/`job_store` override the configured backends (tests).
# Used by app_factory and the tests.
def create_app(settings: Settings | None = None, *, catalog=None, job_store=None) -> FastAPI:
    settings = settings or Settings.from_env()
    outputs = OutputManager(settings.output_root)
    service = ProcessingService(
        settings,
        catalog if catalog is not None else build_catalog(settings),
        job_store if job_store is not None else build_job_store(settings),
        outputs,
    )
    workers = WorkerPool(service)

    # Startup: create the output root, remove expired outputs (failures surface in /ready) and run
    # the async workers until shutdown.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            outputs.ensure_root()
            outputs.cleanup(settings.output_ttl_seconds)
        except OSError:
            log.exception("output root is not usable; /ready will report it")
        workers.start()
        try:
            yield
        finally:
            workers.stop()

    app = FastAPI(
        title="CyberGIS Spatial Processing API (custom, non-OGC)",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.workers = workers
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    if settings.root_path:
        app.add_middleware(RootPathMiddleware, root_path=settings.root_path)

    # Structured response for ApiError (5xx are logged).
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        if exc.status >= 500:
            log.warning("request failed: %s %s", exc.code, exc.message)
        return _error_response(exc)

    # 422 INVALID_REQUEST listing problem locations and messages, never the submitted values.
    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        problems = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ][:20]
        return _error_response(ApiError(422, "INVALID_REQUEST", "request validation failed", {"problems": problems}))

    # Starlette 404/405 etc. as structured errors (NOT_FOUND, METHOD_NOT_ALLOWED, HTTP_<status>).
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _error_response(ApiError(exc.status_code, code, message))

    # Anything unexpected becomes 500 INTERNAL_ERROR without details.
    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error")
        return _error_response(ApiError(500, "INTERNAL_ERROR", "internal server error"))

    @app.post(
        "/operations/clip",
        tags=["operations"],
        summary="Clip a catalogued raster to an AOI and export GeoTIFF/COG",
        responses={200: {"model": JobResponse}, 202: {"model": JobResponse}, **_ERROR_RESPONSES},
    )
    def clip(req: ClipRequest, response: Response) -> dict[str, Any]:
        """Clips the dataset to the AOI (pixel-center rule) and returns the completed job with download links."""
        return _respond(response, *service.clip(req))

    @app.post(
        "/operations/zonal-statistics",
        tags=["operations"],
        summary="Zonal statistics (sum, mean, count of valid pixels) over an AOI",
        responses={200: {"model": ZonalStatisticsResponse}, 202: {"model": JobResponse}, **_ERROR_RESPONSES},
    )
    def zonal_statistics(req: ZonalStatisticsRequest, response: Response) -> dict[str, Any]:
        """Computes statistics over pixels whose centers fall in the AOI; NoData is excluded and counted."""
        return _respond(response, *service.zonal_statistics(req))

    @app.get(
        "/operations/{job_id}",
        tags=["jobs"],
        summary="Job status, result and error",
        responses={200: {"model": JobResponse}, **_ERROR_RESPONSES},
    )
    def get_job(job_id: uuid.UUID) -> dict[str, Any]:
        """Returns the recorded job (queued, running, completed or failed)."""
        return service.get_job(job_id)

    @app.post(
        "/operations/{job_id}/cancel",
        tags=["jobs"],
        summary="Cancel a queued or running background (async) job",
        responses={200: {"model": JobResponse}, **_ERROR_RESPONSES},
    )
    def cancel_job(job_id: uuid.UUID) -> dict[str, Any]:
        """A queued job fails at once with CANCELLED; a running one stops at its next checkpoint."""
        return service.cancel_job(job_id)

    @app.get(
        "/downloads/{job_id}",
        tags=["downloads"],
        summary="Download the GeoTIFF produced by a completed clip job",
        response_class=FileResponse,
        responses={200: {"content": {"image/tiff": {}}, "description": "GeoTIFF/COG file"}, **_ERROR_RESPONSES},
    )
    def download(job_id: uuid.UUID):
        """Streams the clip file; 409 if the job is not a completed clip, 410 once the output has expired."""
        path, media_type = service.download(job_id)
        return FileResponse(path, media_type=media_type, filename=path.name, headers=_NOSNIFF)

    @app.get(
        "/downloads/{job_id}/metadata",
        tags=["downloads"],
        summary="Metadata sidecar (JSON) for a completed clip job",
        response_class=FileResponse,
        responses={200: {"content": {"application/json": {}}}, **_ERROR_RESPONSES},
    )
    def download_metadata(job_id: uuid.UUID):
        """Returns the JSON sidecar written next to the clip (inputs, output grid, software versions)."""
        path = service.download_metadata(job_id)
        return FileResponse(path, media_type="application/json", filename=path.name, headers=_NOSNIFF)

    @app.get("/health", tags=["health"], summary="Liveness: the process is up")
    def health() -> dict[str, str]:
        """Always 200 while the process runs."""
        return {"status": "ok", "service": "spatial-processing", "version": __version__}

    @app.get("/ready", tags=["health"], summary="Readiness: catalog, job store and storage reachable")
    def ready():
        """200 when asset root, writable output root, catalog, job store and async workers are ok; 503 otherwise."""
        state = service.readiness()
        state["async"] = {"enabled": service.async_enabled, "workers_alive": workers.alive,
                          "workers_configured": settings.async_workers}
        if settings.async_workers > 0 and workers.alive < settings.async_workers:
            state["ready"] = False  # a worker thread died: let the orchestrator restart the container
        return JSONResponse(status_code=200 if state["ready"] else 503, content=state)

    return app


# Entry point for `uvicorn --factory spatial_processing.app:app_factory` (Dockerfile CMD).
def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
