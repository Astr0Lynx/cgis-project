"""Trusted dataset catalog lookup.

The only source of raster file locations is the catalog (``catalog.current_datasets``
in PostGIS, per contracts/SHARED_CONTRACTS.md). Clients send a dataset ID, never a path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import errors
from .errors import ApiError

# Logical slug: letters, digits, '.', '_', '-'; must start alphanumeric. Rejects paths, URLs, whitespace.
DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class DatasetRecord:
    dataset_id: str
    version: int
    kind: str
    status: str
    crs: str | None
    units: str | None
    nodata: float | None
    asset_path: str | None
    title: str = ""
    year: int | None = None
    product_level: str | None = None
    source: str | None = None
    checksum_sha256: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def public_metadata(self) -> dict[str, Any]:
        """Metadata safe to echo to clients (no server-side path)."""
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "title": self.title,
            "year": self.year,
            "product_level": self.product_level,
            "units": self.units,
            "catalog_crs": self.crs,
            "catalog_nodata": self.nodata,
            "source": self.source,
            "checksum_sha256": self.checksum_sha256,
            "provenance": self.provenance,
        }


def validate_dataset_id(dataset_id: str) -> None:
    if not isinstance(dataset_id, str) or not DATASET_ID_RE.match(dataset_id):
        raise ApiError(
            422,
            errors.INVALID_DATASET_ID,
            "dataset_id must be a catalog slug (letters, digits, '.', '_', '-'; max 128 chars); "
            "paths and URLs are not accepted",
        )


class Catalog(Protocol):
    def get_ready(self, dataset_id: str) -> DatasetRecord:
        """Return the current ready raster/vector record or raise ApiError."""

    def ping(self) -> None:
        """Raise if the catalog is unreachable."""


def _check_record(record: DatasetRecord) -> DatasetRecord:
    if record.status != "ready":
        raise ApiError(
            409,
            errors.DATASET_NOT_READY,
            f"dataset '{record.dataset_id}' is not ready (status: {record.status})",
            {"dataset_id": record.dataset_id, "status": record.status},
        )
    if record.kind != "raster":
        raise ApiError(
            422,
            errors.DATASET_NOT_RASTER,
            f"dataset '{record.dataset_id}' is a {record.kind} dataset; raster operations need a raster",
            {"dataset_id": record.dataset_id, "kind": record.kind},
        )
    return record


class InMemoryCatalog:
    """Catalog backed by a list of records; mirrors the current_datasets view semantics."""

    def __init__(self, records: list[DatasetRecord] | None = None):
        self._records: list[DatasetRecord] = list(records or [])

    def add(self, record: DatasetRecord) -> None:
        self._records.append(record)

    def get_ready(self, dataset_id: str) -> DatasetRecord:
        validate_dataset_id(dataset_id)
        versions = [r for r in self._records if r.dataset_id == dataset_id]
        if not versions:
            raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' was not found in the catalog")
        ready = [r for r in versions if r.status == "ready"]
        if ready:
            return _check_record(max(ready, key=lambda r: r.version))
        return _check_record(max(versions, key=lambda r: r.version))

    def ping(self) -> None:
        return None


def load_static_catalog(path: Path) -> InMemoryCatalog:
    """Development helper: a JSON list of catalog rows (same columns as catalog.datasets)."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    return InMemoryCatalog([record_from_row(row) for row in rows])


def record_from_row(row: dict[str, Any]) -> DatasetRecord:
    provenance = row.get("provenance") or {}
    if isinstance(provenance, str):
        provenance = json.loads(provenance)
    nodata = row.get("nodata")
    return DatasetRecord(
        dataset_id=row["dataset_id"],
        version=int(row["version"]),
        kind=row["kind"],
        status=row["status"],
        crs=row.get("crs"),
        units=row.get("units"),
        nodata=float(nodata) if nodata is not None else None,
        asset_path=row.get("asset_path"),
        title=row.get("title") or "",
        year=row.get("year"),
        product_level=row.get("product_level"),
        source=row.get("source"),
        checksum_sha256=row.get("checksum_sha256"),
        provenance=provenance,
    )


_COLUMNS = (
    "dataset_id, version, title, kind, product_level, year, crs, units, nodata, "
    "asset_path, checksum_sha256, source, provenance, status"
)


class PostgresCatalog:
    """Reads ``catalog.current_datasets`` (highest ready version) with parameterised SQL only."""

    def __init__(self, conninfo: str | None):
        self._conninfo = conninfo or ""

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._conninfo, row_factory=dict_row, connect_timeout=5)

    def get_ready(self, dataset_id: str) -> DatasetRecord:
        validate_dataset_id(dataset_id)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM catalog.current_datasets WHERE dataset_id = %s",
                    (dataset_id,),
                ).fetchone()
                if row is None:
                    # Distinguish "unknown" from "exists but nothing ready" for a clearer error.
                    row = conn.execute(
                        f"SELECT {_COLUMNS} FROM catalog.datasets WHERE dataset_id = %s "
                        "ORDER BY version DESC LIMIT 1",
                        (dataset_id,),
                    ).fetchone()
        except ApiError:
            raise
        except Exception as exc:  # psycopg.Error, network errors
            raise ApiError(503, errors.CATALOG_UNAVAILABLE, "dataset catalog is unavailable") from exc
        if row is None:
            raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' was not found in the catalog")
        return _check_record(record_from_row(dict(row)))

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1 FROM catalog.current_datasets LIMIT 0")
