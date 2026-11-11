# ApiError and the stable error codes. Every error body is {"error": {code, message, details?}}; messages
# never contain paths, SQL, stack traces, passwords or tokens.

from __future__ import annotations

from typing import Any


# HTTP error raised anywhere in the service; rendered by app._error_response.
class ApiError(Exception):
    # Store status, code, message, optional details and extra response headers.
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}

    # The JSON error envelope. Used by app._error_response.
    def to_body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return {"error": err}


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
