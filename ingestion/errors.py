"""Typed ingestion errors. Every error carries a stable machine-readable ``code``."""

from __future__ import annotations

from typing import Any


class IngestionError(Exception):
    """Base class; ``code`` is stable and safe to show to operators."""

    default_code = "ingestion_error"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class ManifestError(IngestionError):
    """The manifest (or one entry of it) is malformed or references a missing file."""

    default_code = "invalid_manifest"


class ValidationError(IngestionError):
    """The source file is unreadable or inconsistent with the metadata contract."""

    default_code = "validation_failed"


class ConflictError(IngestionError):
    """Same dataset_id/version already exists with different content, or is locked."""

    default_code = "conflict"


class AssetError(IngestionError):
    """Managed asset could not be written, verified or located safely."""

    default_code = "asset_error"


class CatalogError(IngestionError):
    """Database interaction failed or the catalog schema is incompatible."""

    default_code = "catalog_error"
