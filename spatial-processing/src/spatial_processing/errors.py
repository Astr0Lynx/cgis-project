"""Structured error model: every failure is ``{"error": {"code", "message", "details"?}}``.

Messages are written for API clients and never contain server filesystem paths,
SQL, stack traces or credentials.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An error with a stable machine-readable code and an HTTP status."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def to_body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"error": err}


# Error codes (documented in docs/API.md). Grouped by HTTP status.
# 400/422 client input
INVALID_REQUEST = "INVALID_REQUEST"
INVALID_DATASET_ID = "INVALID_DATASET_ID"
INVALID_GEOMETRY = "INVALID_GEOMETRY"
EMPTY_GEOMETRY = "EMPTY_GEOMETRY"
UNSUPPORTED_GEOMETRY_TYPE = "UNSUPPORTED_GEOMETRY_TYPE"
NON_FINITE_COORDINATES = "NON_FINITE_COORDINATES"
COORDINATES_OUT_OF_RANGE = "COORDINATES_OUT_OF_RANGE"
UNSUPPORTED_CRS = "UNSUPPORTED_CRS"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
UNIT_SEMANTICS = "UNIT_SEMANTICS"
AOI_OUTSIDE_RASTER = "AOI_OUTSIDE_RASTER"
NO_PIXELS_SELECTED = "NO_PIXELS_SELECTED"
DATASET_NOT_RASTER = "DATASET_NOT_RASTER"
# 404 / 409 / 410 lookup and state
DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
DATASET_NOT_READY = "DATASET_NOT_READY"
JOB_NOT_FOUND = "JOB_NOT_FOUND"
JOB_NOT_COMPLETED = "JOB_NOT_COMPLETED"
JOB_FAILED = "JOB_FAILED"
NO_DOWNLOAD = "NO_DOWNLOAD"
OUTPUT_EXPIRED = "OUTPUT_EXPIRED"
# 413 / 503 / 504 guardrails
REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
AOI_TOO_COMPLEX = "AOI_TOO_COMPLEX"
AOI_TOO_LARGE = "AOI_TOO_LARGE"
OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
BUSY = "BUSY"
JOB_TIMEOUT = "JOB_TIMEOUT"
# 500 server-side asset/catalog problems (details deliberately withheld)
ASSET_PATH_REJECTED = "ASSET_PATH_REJECTED"
ASSET_MISSING = "ASSET_MISSING"
ASSET_UNREADABLE = "ASSET_UNREADABLE"
CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
JOB_STORE_UNAVAILABLE = "JOB_STORE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
