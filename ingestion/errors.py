# Ingestion error types. Every error carries a stable machine-readable `code` that the CLI prints
# in its JSON output (tests/run.sh e2e greps for it).

from __future__ import annotations

from typing import Any


class IngestionError(Exception):
    default_code = "ingestion_error"

    # Store message, code (falls back to the class default) and optional details. Raised everywhere.
    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}

    # JSON form {"code", "message"[, "details"]}. Used by cli output and importer failure provenance.
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class ManifestError(IngestionError):  # malformed manifest entry or missing source file
    default_code = "invalid_manifest"


class ValidationError(IngestionError):  # source file unreadable or inconsistent
    default_code = "validation_failed"


class ConflictError(IngestionError):  # same dataset_id/version exists with other content, or is locked
    default_code = "conflict"


class AssetError(IngestionError):  # managed asset could not be written, verified or located safely
    default_code = "asset_error"


class CatalogError(IngestionError):  # database unreachable or schema incompatible
    default_code = "catalog_error"
