"""FastAPI application factory for the CyberGIS application API.

Public routes (through the gateway, without prefix stripping): ``/auth/*``, ``/admin/*``,
``/api/exports*``, ``/api/catalog/*`` and ``/api/app/*`` (health, readiness, OpenAPI, docs).
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import SERVICE_NAME, __version__, errors, identity, security, storage
from .config import Settings
from .db import Database
from .errors import ApiError

log = logging.getLogger("cgis_app")
access_log = logging.getLogger("cgis_app.access")

# Query parameters that look like credentials are refused outright: secrets never belong in URLs, where
# they would end up in browser history, proxy logs and Referer headers.
SECRET_QUERY_PARAMS = {"access_token", "token", "api_key", "apikey", "password", "passwd", "session",
                       "cgis_session", "csrf_token", "auth", "authorization", "key"}
STREAMING_PATHS = ("/admin/datasets/uploads", "/admin/datasets")

DESCRIPTION = """
Application API of the CyberGIS platform (Agent 5): **accounts and roles**, **admin dataset hosting**
(upload → validate → publish → version status), **catalog discovery** and **full/clipped exports**.
This is a project API, **not** an OGC API standard; OGC API Features/Tiles stay at `/ogc/`.

### Roles
| Role | May |
|---|---|
| anonymous | `GET /auth/me`, `GET /api/exports/capabilities`, discover datasets whose access level is `public` |
| `user` | discover/export datasets with access `public` or `users`; manage own tokens/password |
| `admin` | everything a user may, plus `/admin/*` (upload, validate, publish, discard, access policy, jobs) |

Authorization is enforced by this service on every route (and can be enforced at the gateway with
`GET /auth/verify`); hiding a button in a UI is never the control.

### Authentication
* **Browser**: `POST /auth/login {username, password}` sets the `cgis_session` cookie (HttpOnly,
  SameSite=Strict, Secure on HTTPS) and returns `csrf_token`. Send `X-CSRF-Token: <csrf_token>` on every
  POST/PUT/PATCH/DELETE. `GET /auth/me` returns the token again after a page reload.
* **Scripts**: `POST /auth/login {…, "token": true}` returns `access_token`; send `Authorization: Bearer …`.
* **QGIS / GDAL / long-lived clients**: `POST /auth/tokens` creates a personal access token (`read` scope by
  default). Use it as `Authorization: Bearer <token>` or as the *password* of HTTP Basic (any username).
  Account passwords are never accepted through HTTP Basic.
* Credentials in the query string are refused (`400 SECRET_IN_URL`).
* Sessions expire after `APP_SESSION_TTL_SECONDS` (default 8 h) or `APP_SESSION_IDLE_SECONDS` of
  inactivity (default 1 h). Logout, password change, role change and account disabling revoke sessions.

### Errors
Every error is `{"error": {"code", "message", "details"?}}`. 401 = no/expired/revoked credentials
(`UNAUTHENTICATED`, `SESSION_EXPIRED`, `SESSION_REVOKED`, `INVALID_TOKEN`, `INVALID_CREDENTIALS`);
403 = authenticated but not allowed (`FORBIDDEN`, `CSRF_FAILED`, `ORIGIN_FORBIDDEN`); 404 also hides datasets
the caller may not see.

### Jobs
`queued → running → completed | failed | cancelled`; exports later become `expired` when their retention
elapses. `progress` is 0..1 (null = indeterminate). A job interrupted by a worker restart is resumed from
scratch up to `APP_JOB_MAX_ATTEMPTS` times.

### Pagination
List endpoints take `offset` and `limit` and return `{items, total, offset, limit, links.next?}`.
"""

TAGS = [
    {"name": "auth", "description": "Sign in/out, identity, personal access tokens, gateway verification."},
    {"name": "admin", "description": "Admin-only dataset hosting: upload, validate, publish, versions, access."},
    {"name": "catalog", "description": "Path-free discovery of permitted datasets and versions."},
    {"name": "exports", "description": "Full/clipped downloads with dataset, version, output CRS and format."},
    {"name": "service", "description": "Health, readiness and API description."},
]


def _error_response(exc: ApiError) -> JSONResponse:
    headers = dict(exc.headers)
    if exc.status in (401, 403) or exc.status >= 400:
        headers.setdefault("Cache-Control", "no-store")
    return JSONResponse(status_code=exc.status, content=exc.to_body(), headers=headers)


class GuardMiddleware:
    """Refuse secrets in URLs and oversized JSON bodies; add security headers; write a path-only access log."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.monotonic()
        status_holder = {"status": 0}
        method = scope["method"]
        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                extra = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                         (b"referrer-policy", b"no-referrer")]
                if not path.startswith("/api/app/docs"):  # Swagger UI needs its own scripts and styles
                    extra.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                for k, v in extra:
                    if k not in names:
                        headers.append((k, v))
                if (path.startswith("/auth") or path.startswith("/admin")) and b"cache-control" not in names:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            query = scope.get("query_string", b"").decode("latin-1")
            if query:
                from urllib.parse import parse_qsl

                names = {k.lower() for k, _ in parse_qsl(query, keep_blank_values=True)}
                if names & SECRET_QUERY_PARAMS:
                    err = ApiError(400, errors.SECRET_IN_URL, "credentials must not be sent in the URL; use the "
                                                              "Authorization header or the session cookie")
                    return await _error_response(err)(scope, receive, send_wrapper)
            if method in ("POST", "PUT", "PATCH") and path not in STREAMING_PATHS:
                headers = dict(scope["headers"])
                length = headers.get(b"content-length")
                limit = self.settings.max_request_bytes
                too_large = ApiError(413, errors.REQUEST_TOO_LARGE, f"request body exceeds {limit} bytes")
                if length is not None and (not length.isdigit() or int(length) > limit):
                    return await _error_response(too_large)(scope, receive, send_wrapper)
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > limit:
                        return await _error_response(too_large)(scope, receive, send_wrapper)
                    if not message.get("more_body", False):
                        break
                replayed = False

                async def replay():
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                return await self.app(scope, replay, send_wrapper)
            return await self.app(scope, receive, send_wrapper)
        finally:
            principal = scope.get("state", {}).get("principal") if isinstance(scope.get("state"), dict) else None
            access_log.info('%s %s %s %.3fs user=%s', method, path[:300], status_holder["status"],
                            time.monotonic() - started, getattr(principal, "username", "-"))


def _maintenance(app: FastAPI, stop: threading.Event) -> None:
    """Hourly: drop long-expired sessions and stale login counters."""
    while not stop.wait(3600):
        try:
            with app.state.db.connection() as conn:
                n = identity.purge_expired_sessions(conn)
                conn.execute("DELETE FROM cgis_app.login_attempts WHERE window_started_at < now() - interval '1 day' "
                             "AND (locked_until IS NULL OR locked_until < now())")
            if n:
                log.info("purged %d expired sessions", n)
        except Exception:
            log.exception("session maintenance failed")


def create_app(settings: Settings | None = None, *, db: Database | None = None, start_maintenance: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    security.configure(settings.hash_concurrency)
    database = db or Database(settings.database_url, min_size=settings.db_pool_min, max_size=settings.db_pool_max,
                              application_name="cgis-app-api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        try:
            app.state.staging_root = storage.ensure_root(settings.staging_root)
        except (OSError, storage.StorageError):
            log.exception("staging root is not usable; uploads will fail and /api/app/ready reports it")
            app.state.staging_root = settings.staging_root
        database.open(wait=False)
        thread = None
        if start_maintenance:
            thread = threading.Thread(target=_maintenance, args=(app, stop), name="cgis-maintenance", daemon=True)
            thread.start()
        log.info("starting %s %s with %s", SERVICE_NAME, __version__,
                 {k: v for k, v in settings.describe().items() if k in (
                     "database_url", "public_base_url", "cookie_secure", "default_dataset_access", "staging_root",
                     "asset_root", "export_root", "max_upload_bytes", "session_ttl_seconds")})
        try:
            yield
        finally:
            stop.set()
            database.close()

    app = FastAPI(title="CyberGIS Application API (auth, admin, catalog, exports)", version=__version__,
                  description=DESCRIPTION, lifespan=lifespan, openapi_tags=TAGS,
                  openapi_url="/api/app/openapi.json", docs_url="/api/app/docs", redoc_url=None)
    app.state.settings = settings
    app.state.db = database
    app.state.staging_root = settings.staging_root
    app.add_middleware(GuardMiddleware, settings=settings)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        if exc.status >= 500:
            log.warning("request failed: %s %s", exc.code, exc.message)
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        problems = [{"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", ""), "type": e.get("type", "")}
                    for e in exc.errors()][:20]  # locations and messages only, never submitted values
        return _error_response(ApiError(422, errors.INVALID_REQUEST, "request validation failed",
                                        {"problems": problems}))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        codes = {404: errors.NOT_FOUND, 405: errors.METHOD_NOT_ALLOWED, 416: errors.RANGE_NOT_SATISFIABLE}
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _error_response(ApiError(exc.status_code, codes.get(exc.status_code, f"HTTP_{exc.status_code}"),
                                        message, headers=dict(exc.headers or {})))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error_response(ApiError(500, errors.INTERNAL_ERROR, "internal server error"))

    from .routes_admin import router as admin_router
    from .routes_auth import router as auth_router
    from .routes_catalog import router as catalog_router
    from .routes_exports import router as exports_router

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(catalog_router)
    app.include_router(exports_router)

    @app.get("/api/app/health", tags=["service"], summary="Liveness")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_NAME, "version": __version__}

    @app.get("/api/app/ready", tags=["service"], summary="Readiness: database, schema and storage roots")
    def ready():
        checks: dict[str, str] = {}
        try:
            database.ping()
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"
        for name, root in (("staging_root", settings.staging_root), ("asset_root", settings.asset_root)):
            checks[name] = "ok" if root.is_dir() else "missing"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "checks": checks})

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes,
                             tags=TAGS)
        schema["servers"] = [{"url": "/", "description": "Through the platform gateway (e.g. http://localhost:8080)"}]
        comps = schema.setdefault("components", {})
        comps.setdefault("securitySchemes", {}).update({
            "sessionCookie": {"type": "apiKey", "in": "cookie", "name": settings.session_cookie,
                              "description": "Browser session from POST /auth/login. Unsafe methods also need "
                                             "the X-CSRF-Token header."},
            "bearer": {"type": "http", "scheme": "bearer", "description": "access_token from POST /auth/login "
                                                                         "(token=true) or a personal access token."},
            "tokenBasic": {"type": "http", "scheme": "basic", "description": "Any username + a personal access "
                                                                            "token as the password (QGIS/GDAL)."},
        })
        public = {("/auth/login", "post"), ("/auth/me", "get"), ("/api/exports/capabilities", "get"),
                  ("/api/app/health", "get"), ("/api/app/ready", "get"), ("/auth/logout", "post")}
        anonymous_ok = {("/api/catalog/datasets", "get"), ("/api/catalog/datasets/{dataset_id}", "get"),
                        ("/api/catalog/datasets/{dataset_id}/versions/{version}", "get")}
        for path, item in schema.get("paths", {}).items():
            for method, op in item.items():
                if (path, method) in public:
                    op["security"] = []
                elif (path, method) in anonymous_ok:
                    op["security"] = [{}, {"sessionCookie": []}, {"bearer": []}, {"tokenBasic": []}]
                else:
                    op["security"] = [{"sessionCookie": []}, {"bearer": []}, {"tokenBasic": []}]
                role = "admin" if path.startswith("/admin") else ("user" if (path, method) not in public | anonymous_ok
                                                                   else "anonymous")
                op["x-required-role"] = role
        schema["x-upload-limits"] = {"raster_max_bytes": settings.max_upload_bytes,
                                     "geojson_max_bytes": settings.max_vector_upload_bytes,
                                     "json_body_max_bytes": settings.max_request_bytes}
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


def app_factory() -> FastAPI:
    """``uvicorn --factory cgis_app.app:app_factory``."""
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app(settings)
