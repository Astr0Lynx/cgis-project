"""Normalized error envelope ``{"error": {"code", "message", "details"?}}`` (same as /api/operations).

Messages are written for API clients: they never contain filesystem paths, SQL, stack traces,
passwords, tokens or session identifiers.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}

    def to_body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"error": err}

    def to_dict(self) -> dict[str, Any]:
        return self.to_body()["error"]


# 400 / 422 request problems
INVALID_REQUEST = "INVALID_REQUEST"
INVALID_METADATA = "INVALID_METADATA"
INVALID_DATASET_ID = "INVALID_DATASET_ID"
SECRET_IN_URL = "SECRET_IN_URL"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
UNSUPPORTED_CRS = "UNSUPPORTED_CRS"
INVALID_GEOMETRY = "INVALID_GEOMETRY"
AOI_REQUIRED = "AOI_REQUIRED"
AOI_NOT_ALLOWED = "AOI_NOT_ALLOWED"
AOI_OUTSIDE_DATASET = "AOI_OUTSIDE_DATASET"
WEAK_PASSWORD = "WEAK_PASSWORD"
# 401 / 403 identity and permission
UNAUTHENTICATED = "UNAUTHENTICATED"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
SESSION_EXPIRED = "SESSION_EXPIRED"
SESSION_REVOKED = "SESSION_REVOKED"
INVALID_TOKEN = "INVALID_TOKEN"
FORBIDDEN = "FORBIDDEN"
CSRF_FAILED = "CSRF_FAILED"
ORIGIN_FORBIDDEN = "ORIGIN_FORBIDDEN"
ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
# 404 / 405 / 409 / 410 state
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
DATASET_NOT_READY = "DATASET_NOT_READY"
JOB_NOT_FOUND = "JOB_NOT_FOUND"
TOKEN_NOT_FOUND = "TOKEN_NOT_FOUND"
VERSION_EXISTS = "VERSION_EXISTS"
CHECKSUM_CONFLICT = "CHECKSUM_CONFLICT"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
KIND_CONFLICT = "KIND_CONFLICT"
INVALID_STATE = "INVALID_STATE"
READY_IMMUTABLE = "READY_IMMUTABLE"
JOB_NOT_COMPLETED = "JOB_NOT_COMPLETED"
JOB_FAILED = "JOB_FAILED"
JOB_FINISHED = "JOB_FINISHED"
EXPORT_EXPIRED = "EXPORT_EXPIRED"
# 413 / 415 / 416 / 429 / 503 / 507 guardrails
FILE_TOO_LARGE = "FILE_TOO_LARGE"
REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
RANGE_NOT_SATISFIABLE = "RANGE_NOT_SATISFIABLE"
AOI_TOO_LARGE = "AOI_TOO_LARGE"
AOI_TOO_COMPLEX = "AOI_TOO_COMPLEX"
OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
TOO_MANY_JOBS = "TOO_MANY_JOBS"
TOO_MANY_TOKENS = "TOO_MANY_TOKENS"
STORAGE_FULL = "STORAGE_FULL"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
