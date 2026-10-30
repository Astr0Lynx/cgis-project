"""FastAPI application: custom (non-OGC) spatial processing routes.

Internal routes are unprefixed (``/operations/...``, ``/downloads/...``); the gateway
exposes them under ``/api/`` and strips the prefix. ``ROOT_PATH=/api`` makes the
OpenAPI document and generated links use the public prefix.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, errors
from .catalog import Catalog, InMemoryCatalog, PostgresCatalog, load_static_catalog
from .config import Settings
from .errors import ApiError
from .jobs import InMemoryJobStore, JobStore, PostgresJobStore
from .models import (
    ClipRequest,
    ErrorResponse,
    JobResponse,
    ZonalStatisticsRequest,
    ZonalStatisticsResponse,
)
from .outputs import OutputManager
from .service import ProcessingService

log = logging.getLogger("spatial_processing")

DESCRIPTION = """
Project-specific processing API for CyberGIS Phase 2 (**not** an OGC API standard endpoint).

* Datasets are referenced by catalog `dataset_id` only; raster files are resolved server-side from
  the PostGIS catalog (`catalog.current_datasets`, status `ready`). Paths and URLs are never accepted.
* AOIs are GeoJSON Polygon/MultiPolygon in x/y (lon/lat) order with an explicit `aoi_crs` (`EPSG:<code>`).
* **Pixel selection:** a pixel is included iff its **center** lies inside the AOI (`all_touched=false`).
  NoData pixels are excluded and reported (`nodata_excluded`), never treated as zero.
* Operations run synchronously but are recorded as jobs (`queued → running → completed|failed`).
* Errors are `{"error": {"code", "message", "details"?}}`.
"""

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (404, 409, 410, 413, 422, 500, 503, 504)
}


def _error_response(exc: ApiError) -> JSONResponse:
    headers = {}
    if exc.code == errors.BUSY:
        headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 5))
    return JSONResponse(status_code=exc.status, content=exc.to_body(), headers=headers)


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` (with or without Content-Length)."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        too_large = ApiError(
            413, errors.REQUEST_TOO_LARGE, f"request body exceeds {self.max_bytes} bytes"
        )
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            return await _error_response(too_large)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                return await _error_response(too_large)(scope, receive, send)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


class RootPathMiddleware:
    """Declare the public prefix (e.g. ``/api``) unless the server already set one.

    Starlette then routes both ``/api/operations/...`` (gateway forwards the prefix) and
    ``/operations/...`` (gateway strips it), and OpenAPI ``servers`` shows the prefix.
    """

    def __init__(self, app, root_path: str):
        self.app = app
        self.root_path = root_path.rstrip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not scope.get("root_path"):
            scope = {**scope, "root_path": self.root_path}
        return await self.app(scope, receive, send)


def build_catalog(settings: Settings) -> Catalog:
    if settings.catalog_backend == "postgres":
        return PostgresCatalog(settings.database_url)
    if settings.catalog_backend == "static" and settings.static_catalog_file:
        return load_static_catalog(settings.static_catalog_file)
    if settings.catalog_backend == "memory":
        return InMemoryCatalog()
    raise ValueError(f"unsupported CATALOG_BACKEND {settings.catalog_backend!r}")


def build_job_store(settings: Settings) -> JobStore:
    if settings.job_store_backend == "postgres":
        return PostgresJobStore(settings.database_url)
    if settings.job_store_backend == "memory":
        return InMemoryJobStore()
    raise ValueError(f"unsupported JOB_STORE_BACKEND {settings.job_store_backend!r}")


def create_app(
    settings: Settings | None = None,
    *,
    catalog: Catalog | None = None,
    job_store: JobStore | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    outputs = OutputManager(settings.output_root)
    service = ProcessingService(
        settings,
        catalog if catalog is not None else build_catalog(settings),
        job_store if job_store is not None else build_job_store(settings),
        outputs,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            outputs.ensure_root()
            outputs.cleanup(settings.output_ttl_seconds)
        except OSError:
            log.exception("output root is not usable; /ready will report it")
        yield

    app = FastAPI(
        title="CyberGIS Spatial Processing API (custom, non-OGC)",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.service = service
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    if settings.root_path:
        app.add_middleware(RootPathMiddleware, root_path=settings.root_path)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        if exc.status >= 500:
            log.warning("request failed: %s %s", exc.code, exc.message)
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # Echo locations and messages only, never the submitted values.
        problems = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ][:20]
        return _error_response(
            ApiError(422, errors.INVALID_REQUEST, "request validation failed", {"problems": problems})
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _error_response(ApiError(exc.status_code, codes.get(exc.status_code, f"HTTP_{exc.status_code}"), message))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error")
        return _error_response(ApiError(500, errors.INTERNAL_ERROR, "internal server error"))

    @app.post(
        "/operations/clip",
        tags=["operations"],
        summary="Clip a catalogued raster to an AOI and export GeoTIFF/COG",
        responses={200: {"model": JobResponse}, **_ERROR_RESPONSES},
    )
    def clip(req: ClipRequest) -> dict[str, Any]:
        return service.clip(req)

    @app.post(
        "/operations/zonal-statistics",
        tags=["operations"],
        summary="Zonal statistics (sum, mean, count of valid pixels) over an AOI",
        responses={200: {"model": ZonalStatisticsResponse}, **_ERROR_RESPONSES},
    )
    def zonal_statistics(req: ZonalStatisticsRequest) -> dict[str, Any]:
        return service.zonal_statistics(req)

    @app.get(
        "/operations/{job_id}",
        tags=["jobs"],
        summary="Job status, result and error",
        responses={200: {"model": JobResponse}, **_ERROR_RESPONSES},
    )
    def get_job(job_id: uuid.UUID) -> dict[str, Any]:
        return service.get_job(job_id)

    @app.get(
        "/downloads/{job_id}",
        tags=["downloads"],
        summary="Download the GeoTIFF produced by a completed clip job",
        response_class=FileResponse,
        responses={200: {"content": {"image/tiff": {}}, "description": "GeoTIFF/COG file"}, **_ERROR_RESPONSES},
    )
    def download(job_id: uuid.UUID):
        path, filename, media_type = service.download(job_id)
        return FileResponse(
            path, media_type=media_type, filename=filename, headers={"X-Content-Type-Options": "nosniff"}
        )

    @app.get(
        "/downloads/{job_id}/metadata",
        tags=["downloads"],
        summary="Metadata sidecar (JSON) for a completed clip job",
        response_class=FileResponse,
        responses={200: {"content": {"application/json": {}}}, **_ERROR_RESPONSES},
    )
    def download_metadata(job_id: uuid.UUID):
        path, filename = service.download_metadata(job_id)
        return FileResponse(
            path, media_type="application/json", filename=filename, headers={"X-Content-Type-Options": "nosniff"}
        )

    @app.get("/health", tags=["health"], summary="Liveness: the process is up")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "spatial-processing", "version": __version__}

    @app.get("/ready", tags=["health"], summary="Readiness: catalog, job store and storage reachable")
    def ready():
        state = service.readiness()
        return JSONResponse(status_code=200 if state["ready"] else 503, content=state)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory spatial_processing.app:app_factory``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
