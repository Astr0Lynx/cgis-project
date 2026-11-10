"""Runtime configuration read once from ``APP_*`` environment variables.

Secrets (database passwords, bootstrap passwords) are never logged; :meth:`Settings.describe` returns a
redacted view that is safe for startup logs. Every limit has a conservative default sized for the
16 GB local Docker target.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

GIB = 1024**3
MIB = 1024**2

ACCESS_LEVELS = ("public", "users", "admins")


class ConfigError(ValueError):
    """Invalid configuration; the message names the variable but never echoes a secret."""


def _str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else raw.strip()


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false")


def read_secret(name: str) -> str | None:
    """``NAME`` or the contents of the file named by ``NAME_FILE`` (Docker/Compose secrets)."""
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        try:
            return Path(file_var).read_text(encoding="utf-8").strip() or None
        except OSError:
            raise ConfigError(f"{name}_FILE cannot be read") from None
    raw = os.environ.get(name)
    return raw if raw else None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ConfigError("APP_PUBLIC_BASE_URL must be an absolute http(s) URL")
    return f"{parts.scheme}://{parts.netloc}".lower()


def _loopback_aliases(origin: str) -> tuple[str, ...]:
    """A local stack is opened as either localhost or 127.0.0.1; accept both spellings (loopback only)."""
    parts = urlsplit(origin)
    host, port = parts.hostname or "", f":{parts.port}" if parts.port else ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return tuple(f"{parts.scheme}://{h}{port}" for h in ("localhost", "127.0.0.1", "[::1]"))
    return ()


@dataclass(frozen=True)
class Settings:
    # --- connections
    database_url: str = ""  # libpq conninfo; empty = libpq PG* variables
    db_pool_min: int = 1
    db_pool_max: int = 8

    # --- public identity (cookie flags, Origin checks, absolute links)
    public_base_url: str = "http://localhost:8080"
    allowed_origins: tuple[str, ...] = ()
    cookie_secure: bool = False
    trust_proxy_headers: bool = False

    # --- sessions and tokens
    session_cookie: str = "cgis_session"
    session_ttl_seconds: int = 8 * 3600
    session_idle_seconds: int = 3600
    token_default_ttl_days: int = 30
    token_max_ttl_days: int = 90
    max_tokens_per_user: int = 20
    password_min_length: int = 12
    login_max_failures: int = 5
    login_window_seconds: int = 900
    login_lockout_seconds: int = 900
    ip_max_failures: int = 50
    hash_concurrency: int = 2

    # --- access policy
    default_dataset_access: str = "users"

    # --- storage roots (server-owned; never client supplied)
    staging_root: Path = Path("/data/staging")
    asset_root: Path = Path("/data/assets")
    export_root: Path = Path("/data/exports")

    # --- upload guardrails
    max_upload_bytes: int = 8 * GIB  # rasters
    max_vector_upload_bytes: int = 256 * MIB  # GeoJSON is parsed in memory by the importer
    max_metadata_bytes: int = 64 * 1024
    staging_max_total_bytes: int = 32 * GIB
    min_free_bytes: int = 2 * GIB
    max_raster_pixels: int = 3_000_000_000
    max_raster_uncompressed_bytes: int = 16 * GIB
    max_raster_bands: int = 16
    max_compression_ratio: float = 2000.0
    staging_ttl_seconds: int = 7 * 24 * 3600

    # --- export guardrails
    export_ttl_seconds: int = 24 * 3600
    export_max_output_bytes: int = 8 * GIB
    export_max_total_bytes: int = 32 * GIB
    export_max_pixels: int = 3_000_000_000
    export_clip_max_pixels: int = 25_000_000
    export_max_seconds: int = 3600
    export_max_active_per_user: int = 4
    max_aoi_vertices: int = 10_000
    max_aoi_parts: int = 100
    max_request_bytes: int = 1_000_000  # JSON bodies (uploads have their own limit)

    # --- workers
    worker_poll_seconds: float = 2.0
    job_lease_seconds: int = 120
    job_max_attempts: int = 3
    gdal_cachemax_mb: int = 256

    log_level: str = "INFO"
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def public_origin(self) -> str:
        return _origin(self.public_base_url)

    @classmethod
    def from_env(cls) -> "Settings":
        public = _str("APP_PUBLIC_BASE_URL", _str("PUBLIC_BASE_URL", cls.public_base_url))
        origin = _origin(public)
        extra_origins = tuple(
            _origin(o.strip()) for o in (_str("APP_ALLOWED_ORIGINS", "") or "").split(",") if o.strip()
        )
        access = _str("APP_DEFAULT_DATASET_ACCESS", cls.default_dataset_access)
        if access not in ACCESS_LEVELS:
            raise ConfigError(f"APP_DEFAULT_DATASET_ACCESS must be one of {', '.join(ACCESS_LEVELS)}")
        secure_default = public.lower().startswith("https://")
        settings = cls(
            database_url=read_secret("APP_DATABASE_URL") or "",
            db_pool_min=_int("APP_DB_POOL_MIN", cls.db_pool_min, 0),
            db_pool_max=_int("APP_DB_POOL_MAX", cls.db_pool_max, 1),
            public_base_url=public.rstrip("/"),
            allowed_origins=tuple(dict.fromkeys((origin, *_loopback_aliases(origin), *extra_origins))),
            cookie_secure=_bool("APP_COOKIE_SECURE", secure_default),
            trust_proxy_headers=_bool("APP_TRUST_PROXY_HEADERS", cls.trust_proxy_headers),
            session_ttl_seconds=_int("APP_SESSION_TTL_SECONDS", cls.session_ttl_seconds, 60),
            session_idle_seconds=_int("APP_SESSION_IDLE_SECONDS", cls.session_idle_seconds, 60),
            token_default_ttl_days=_int("APP_TOKEN_DEFAULT_TTL_DAYS", cls.token_default_ttl_days, 1),
            token_max_ttl_days=_int("APP_TOKEN_MAX_TTL_DAYS", cls.token_max_ttl_days, 1),
            max_tokens_per_user=_int("APP_MAX_TOKENS_PER_USER", cls.max_tokens_per_user, 1),
            password_min_length=_int("APP_PASSWORD_MIN_LENGTH", cls.password_min_length, 8),
            login_max_failures=_int("APP_LOGIN_MAX_FAILURES", cls.login_max_failures, 1),
            login_window_seconds=_int("APP_LOGIN_WINDOW_SECONDS", cls.login_window_seconds, 1),
            login_lockout_seconds=_int("APP_LOGIN_LOCKOUT_SECONDS", cls.login_lockout_seconds, 1),
            ip_max_failures=_int("APP_IP_MAX_FAILURES", cls.ip_max_failures, 1),
            hash_concurrency=_int("APP_HASH_CONCURRENCY", cls.hash_concurrency, 1),
            default_dataset_access=access,
            staging_root=Path(_str("APP_STAGING_ROOT", str(cls.staging_root))),
            asset_root=Path(_str("APP_ASSET_ROOT", str(cls.asset_root))),
            export_root=Path(_str("APP_EXPORT_ROOT", str(cls.export_root))),
            max_upload_bytes=_int("APP_MAX_UPLOAD_BYTES", cls.max_upload_bytes, 1),
            max_vector_upload_bytes=_int("APP_MAX_VECTOR_UPLOAD_BYTES", cls.max_vector_upload_bytes, 1),
            staging_max_total_bytes=_int("APP_STAGING_MAX_TOTAL_BYTES", cls.staging_max_total_bytes, 1),
            min_free_bytes=_int("APP_MIN_FREE_BYTES", cls.min_free_bytes, 0),
            max_raster_pixels=_int("APP_MAX_RASTER_PIXELS", cls.max_raster_pixels, 1),
            max_raster_uncompressed_bytes=_int(
                "APP_MAX_RASTER_UNCOMPRESSED_BYTES", cls.max_raster_uncompressed_bytes, 1
            ),
            max_raster_bands=_int("APP_MAX_RASTER_BANDS", cls.max_raster_bands, 1),
            max_compression_ratio=_float("APP_MAX_COMPRESSION_RATIO", cls.max_compression_ratio, 1.0),
            staging_ttl_seconds=_int("APP_STAGING_TTL_SECONDS", cls.staging_ttl_seconds, 60),
            export_ttl_seconds=_int("APP_EXPORT_TTL_SECONDS", cls.export_ttl_seconds, 60),
            export_max_output_bytes=_int("APP_EXPORT_MAX_OUTPUT_BYTES", cls.export_max_output_bytes, 1),
            export_max_total_bytes=_int("APP_EXPORT_MAX_TOTAL_BYTES", cls.export_max_total_bytes, 1),
            export_max_pixels=_int("APP_EXPORT_MAX_PIXELS", cls.export_max_pixels, 1),
            export_clip_max_pixels=_int("APP_EXPORT_CLIP_MAX_PIXELS", cls.export_clip_max_pixels, 1),
            export_max_seconds=_int("APP_EXPORT_MAX_SECONDS", cls.export_max_seconds, 1),
            export_max_active_per_user=_int("APP_EXPORT_MAX_ACTIVE_PER_USER", cls.export_max_active_per_user, 1),
            max_aoi_vertices=_int("APP_MAX_AOI_VERTICES", cls.max_aoi_vertices, 4),
            max_aoi_parts=_int("APP_MAX_AOI_PARTS", cls.max_aoi_parts, 1),
            max_request_bytes=_int("APP_MAX_REQUEST_BYTES", cls.max_request_bytes, 1024),
            worker_poll_seconds=_float("APP_WORKER_POLL_SECONDS", cls.worker_poll_seconds, 0.05),
            job_lease_seconds=_int("APP_JOB_LEASE_SECONDS", cls.job_lease_seconds, 5),
            job_max_attempts=_int("APP_JOB_MAX_ATTEMPTS", cls.job_max_attempts, 1),
            gdal_cachemax_mb=_int("APP_GDAL_CACHEMAX_MB", cls.gdal_cachemax_mb, 16),
            log_level=(_str("APP_LOG_LEVEL", cls.log_level) or "INFO").upper(),
        )
        if settings.token_default_ttl_days > settings.token_max_ttl_days:
            raise ConfigError("APP_TOKEN_DEFAULT_TTL_DAYS must not exceed APP_TOKEN_MAX_TTL_DAYS")
        if settings.session_idle_seconds > settings.session_ttl_seconds:
            raise ConfigError("APP_SESSION_IDLE_SECONDS must not exceed APP_SESSION_TTL_SECONDS")
        return settings

    def describe(self) -> dict:
        """Configuration for logs: no secrets, database URL reduced to host/db."""
        out = {}
        for f in fields(self):
            if f.name in ("extra",):
                continue
            value = getattr(self, f.name)
            if f.name == "database_url":
                value = redact_dsn(value)
            out[f.name] = str(value) if isinstance(value, Path) else value
        return out


def redact_dsn(dsn: str) -> str:
    """Hide passwords in URL and key=value conninfo forms."""
    import re

    if not dsn:
        return "(libpq defaults)"
    dsn = re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", dsn)
    return re.sub(r"(password\s*=\s*)(\S+)", r"\1***", dsn, flags=re.I)
