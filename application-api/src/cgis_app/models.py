"""Request and response schemas. Requests forbid unknown fields, so a client can never smuggle a server
path, URL, role or owner into a request body."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"

_TOWN = {"type": "Polygon",
         "coordinates": [[[78.01, 17.98], [78.02, 17.98], [78.02, 17.99], [78.01, 17.99], [78.01, 17.98]]]}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ errors
class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. UNAUTHENTICATED, VERSION_EXISTS.")
    message: str = Field(description="Human-readable message (never contains paths, SQL or secrets).")
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ------------------------------------------------------------------ auth
class LoginRequest(_Strict):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [{"username": "analyst", "password": "<your password>"},
                     {"username": "qgis-bot", "password": "<your password>", "token": True}]})

    username: str = Field(..., min_length=1, max_length=63)
    password: str = Field(..., min_length=1, max_length=1024, json_schema_extra={"format": "password"})
    token: bool = Field(False, description="false (browsers): set an HttpOnly session cookie and return a CSRF "
                                           "token. true (scripts): return a bearer access_token instead; no cookie.")


class UserInfo(BaseModel):
    user_id: str
    username: str
    role: Literal["admin", "user"]


class Session(BaseModel):
    authenticated: bool
    user: UserInfo | None = None
    expires_at: datetime | None = None
    idle_timeout_seconds: int | None = None
    auth_method: Literal["cookie", "bearer", "token"] | None = None
    csrf_token: str | None = Field(None, description="Browser sessions only: echo as X-CSRF-Token on every "
                                                     "POST/PUT/PATCH/DELETE.")
    access_token: str | None = Field(None, description="Only when login used token=true. Send as "
                                                       "'Authorization: Bearer <access_token>'.")
    token_type: Literal["Bearer"] | None = None


class TokenCreate(_Strict):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [{"name": "QGIS desktop", "expires_in_days": 30, "scope": "read"}]})

    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
    expires_in_days: int | None = Field(None, ge=1, le=365)
    scope: Literal["read", "admin"] = Field("read", description="read: catalog/OGC/tiles/exports as role 'user'. "
                                                                "admin: also admin routes (admin accounts only).")


class TokenInfo(BaseModel):
    token_id: str
    name: str | None
    scope: str
    created_at: datetime
    last_seen_at: datetime | None = None
    expires_at: datetime
    revoked_at: datetime | None = None


class TokenCreated(TokenInfo):
    token: str = Field(description="Shown ONCE. Use 'Authorization: Bearer <token>' (or HTTP Basic with any "
                                   "username and the token as password, for clients without header support).")


class PasswordChange(_Strict):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=1, max_length=1024)


# ------------------------------------------------------------------ admin
class UploadMetadata(_Strict):
    """The manifest entry for an uploaded file (ingestion contract), WITHOUT ``file``: the server decides
    where the bytes live."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "dataset_id": "india-l1-pop-2011", "version": 2, "title": "India Level 1 population 2011",
        "kind": "raster", "product_level": "level1", "year": 2011, "units": "people_per_cell",
        "source": "CyberGIS Level 1 model run <commit>", "provenance": {"license": "CC BY 4.0"},
        "checksum_sha256": None}]})

    dataset_id: str = Field(..., min_length=1, max_length=128, pattern=SLUG_PATTERN)
    version: int | None = Field(None, ge=1, le=1_000_000, description="Omit to use the next free version.")
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    kind: Literal["raster", "vector"]
    product_level: Literal["source", "level1", "level2", "other"]
    year: int | None = Field(None, ge=1800, le=2200)
    units: str | None = Field(None, min_length=1, max_length=64)
    source: str | None = Field(None, min_length=1, max_length=500)
    provenance: dict[str, Any] = Field(default_factory=dict)
    checksum_sha256: str | None = Field(None, pattern=r"^[0-9a-fA-F]{64}$",
                                        description="Optional expected SHA-256 of the file; verified on receipt.")
    expect: dict[str, Any] | None = Field(None, description="Optional importer assertions (crs, width, height, "
                                                            "dtype, nodata, resolution, bounds, feature_count).")
    band: int | None = Field(None, ge=1, le=64)
    allow_missing_nodata: bool = False
    id_property: str | None = Field(None, min_length=1, max_length=64)
    name_property: str | None = Field(None, min_length=1, max_length=64)
    level_property: str | None = Field(None, min_length=1, max_length=64)
    unit_id_prefix: str | None = Field(None, max_length=32, pattern=r"^[A-Za-z0-9._:-]*$")

    @field_validator("provenance")
    @classmethod
    def _small_provenance(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > 32_768:
            raise ValueError("provenance must be at most 32 KiB of JSON")

        def depth(v, d=0):
            if d > 8:
                raise ValueError("provenance is nested too deeply")
            if isinstance(v, dict):
                for x in v.values():
                    depth(x, d + 1)
            elif isinstance(v, list):
                for x in v:
                    depth(x, d + 1)

        depth(value)
        return value


class AccessUpdate(_Strict):
    access: Literal["public", "users", "admins"] = Field(
        description="public: anonymous discovery, any signed-in user may export; users: signed-in users; "
                    "admins: administrators only.")
    note: str | None = Field(None, max_length=500)


class AdminJob(BaseModel):
    job_id: str
    kind: Literal["upload", "validate", "publish"]
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: float | None = None
    progress_message: str | None = None
    dataset_id: str | None = None
    version: int | None = None
    upload_id: str | None = None
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    links: dict[str, str]


# ------------------------------------------------------------------ exports
class ExportRequest(_Strict):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"dataset_id": "india-l1-pop-2011", "kind": "full", "format": "COG"},
        {"dataset_id": "india-l1-pop-2011", "version": 1, "kind": "full", "format": "GTiff",
         "output_crs": "EPSG:24378"},
        {"dataset_id": "synthetic-worldpop-2001", "kind": "clip", "aoi": _TOWN, "aoi_crs": "EPSG:4326",
         "format": "GTiff"},
        {"dataset_id": "synthetic-towns", "kind": "clip", "aoi": _TOWN, "aoi_crs": "EPSG:4326",
         "format": "GeoJSON"}]})

    dataset_id: str = Field(..., min_length=1, max_length=128)
    version: int | None = Field(None, ge=1, description="Omit for the current ready version.")
    kind: Literal["full", "clip"]
    aoi: dict[str, Any] | None = Field(None, description="GeoJSON Polygon/MultiPolygon (or Feature); required for "
                                                         "kind=clip, refused for kind=full.")
    aoi_crs: str | None = Field(None, max_length=64, description="EPSG:<code> of aoi (default EPSG:4326).")
    output_crs: str | None = Field(None, max_length=64, description="EPSG:<code>; omit for the native CRS.")
    format: str = Field(..., min_length=1, max_length=16, description="Raster: GTiff | COG. Vector: GeoJSON.")


class ExportJob(BaseModel):
    job_id: str
    kind: Literal["export"] = "export"
    status: Literal["queued", "running", "completed", "failed", "cancelled", "expired"]
    progress: float | None = None
    progress_message: str | None = None
    input: dict[str, Any]
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None
    links: dict[str, str]


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int
    offset: int
    limit: int
    links: dict[str, str] = Field(default_factory=dict)
