"""Import manifest parsing (contract: ``contracts/SHARED_CONTRACTS.md`` § Ingestion contract).

A manifest file is one of:

* a single dataset object (the shape shown in the contract),
* a JSON list of dataset objects, or
* an object ``{"datasets": [...], ...}``.

Relative ``file`` paths resolve against the manifest's directory. Spatial properties (CRS, bounds,
NoData, resolution) are always read from the file; the optional ``expect`` block only *asserts* them.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ManifestError

log = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KINDS = ("raster", "vector")
PRODUCT_LEVELS = ("source", "level1", "level2", "other")
EXPECT_KEYS = {"crs", "width", "height", "band_count", "dtype", "nodata", "resolution", "bounds", "tolerance",
               "feature_count"}

KNOWN_KEYS = {
    "dataset_id", "version", "title", "description", "kind", "product_level", "year", "units", "source", "file",
    "provenance", "band", "expect", "checksum_sha256", "allow_missing_nodata",
    "id_property", "name_property", "level_property", "unit_id_prefix",
}


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    version: int
    title: str
    kind: str
    product_level: str
    source: str
    file: Path
    year: int | None = None
    units: str | None = None
    description: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    band: int = 1
    expect: dict[str, Any] = field(default_factory=dict)
    checksum_sha256: str | None = None
    allow_missing_nodata: bool = False
    id_property: str = "unit_id"
    name_property: str = "name"
    level_property: str = "level"
    unit_id_prefix: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.dataset_id}@v{self.version}"

    @property
    def is_synthetic(self) -> bool:
        prov = self.provenance or {}
        return bool(prov.get("synthetic")) or "fixture" in self.source.lower() or "synthetic" in self.source.lower()


def load_manifest(path: str | Path) -> tuple[Path, list[dict[str, Any]]]:
    """Read a manifest file and return ``(base_dir, raw_entries)`` without validating entries."""
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"manifest not found: {p}", code="manifest_not_found")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest {p.name} is not valid JSON: {exc}", code="manifest_not_json") from exc

    if isinstance(doc, list):
        entries = doc
    elif isinstance(doc, dict) and "datasets" in doc:
        entries = doc["datasets"]
    elif isinstance(doc, dict):
        entries = [doc]
    else:
        raise ManifestError("manifest must be an object or a list of objects", code="manifest_shape")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("manifest contains no dataset entries", code="manifest_empty")
    if not all(isinstance(e, dict) for e in entries):
        raise ManifestError("every manifest entry must be a JSON object", code="manifest_shape")

    seen: set[tuple[Any, Any]] = set()
    for e in entries:
        k = (e.get("dataset_id"), e.get("version"))
        if k in seen:
            raise ManifestError(f"duplicate entry {k[0]}@v{k[1]} in manifest", code="manifest_duplicate")
        seen.add(k)
    return p.parent.absolute(), entries


def _req_str(raw: dict, key: str) -> str:
    val = raw.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ManifestError(f"'{key}' is required and must be a non-empty string", code="missing_field",
                            details={"field": key})
    return val.strip()


def _opt_str(raw: dict, key: str) -> str | None:
    val = raw.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise ManifestError(f"'{key}' must be a string", code="invalid_field", details={"field": key})
    return val.strip() or None


def _int(raw: dict, key: str, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    val = raw.get(key)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        raise ManifestError(f"'{key}' must be an integer", code="invalid_field", details={"field": key})
    if (minimum is not None and val < minimum) or (maximum is not None and val > maximum):
        raise ManifestError(f"'{key}'={val} is out of range", code="invalid_field", details={"field": key})
    return val


def parse_entry(raw: dict[str, Any], base_dir: Path) -> DatasetSpec:
    """Validate one manifest entry. Raises :class:`ManifestError` with a specific code."""
    dataset_id = _req_str(raw, "dataset_id")
    if not SLUG_RE.match(dataset_id):
        raise ManifestError(
            f"dataset_id {dataset_id!r} must be a lowercase slug matching {SLUG_RE.pattern}",
            code="invalid_dataset_id")
    version = _int(raw, "version", minimum=1)
    if version is None:
        raise ManifestError("'version' is required and must be a positive integer", code="missing_field",
                            details={"field": "version"})

    kind = _req_str(raw, "kind")
    if kind not in KINDS:
        raise ManifestError(f"unsupported kind {kind!r}; expected one of {list(KINDS)}", code="unsupported_kind")
    product_level = _req_str(raw, "product_level")
    if product_level not in PRODUCT_LEVELS:
        raise ManifestError(f"unsupported product_level {product_level!r}; expected one of {list(PRODUCT_LEVELS)}",
                            code="invalid_product_level")

    title = _req_str(raw, "title")
    source = _req_str(raw, "source")
    units = _opt_str(raw, "units")
    if kind == "raster" and not units:
        raise ManifestError("'units' is required for raster datasets (e.g. people_per_cell)", code="missing_field",
                            details={"field": "units"})

    year = _int(raw, "year", minimum=1000, maximum=9999)
    band = _int(raw, "band", minimum=1)

    file_raw = _req_str(raw, "file")
    file_path = Path(file_raw)
    if not file_path.is_absolute():
        file_path = base_dir / file_path
    if not file_path.is_file():
        raise ManifestError(f"source file not found for {dataset_id}@v{version}: {file_raw}", code="file_not_found",
                            details={"file": file_raw})

    provenance = raw.get("provenance")
    provenance = {} if provenance is None else provenance
    if not isinstance(provenance, dict):
        raise ManifestError("'provenance' must be a JSON object", code="invalid_field", details={"field": "provenance"})

    expect = raw.get("expect")
    expect = {} if expect is None else expect
    if not isinstance(expect, dict):
        raise ManifestError("'expect' must be a JSON object", code="invalid_field", details={"field": "expect"})
    unknown_expect = set(expect) - EXPECT_KEYS
    if unknown_expect:
        raise ManifestError(f"unknown 'expect' keys: {sorted(unknown_expect)}", code="invalid_field",
                            details={"field": "expect"})
    _check_expect_numbers(expect)

    checksum = _opt_str(raw, "checksum_sha256")
    if checksum is not None:
        checksum = checksum.lower()
        if not SHA256_RE.match(checksum):
            raise ManifestError("'checksum_sha256' must be 64 hex characters", code="invalid_field",
                                details={"field": "checksum_sha256"})

    allow_missing_nodata = raw.get("allow_missing_nodata", False)
    if not isinstance(allow_missing_nodata, bool):
        raise ManifestError("'allow_missing_nodata' must be a boolean", code="invalid_field")

    extra = {k: v for k, v in raw.items() if k not in KNOWN_KEYS}
    if extra:
        log.warning("%s@v%s: unrecognised manifest keys kept in provenance.manifest_extra: %s",
                    dataset_id, version, sorted(extra))

    spec = DatasetSpec(
        dataset_id=dataset_id, version=version, title=title, kind=kind, product_level=product_level,
        source=source, file=file_path, year=year, units=units, description=_opt_str(raw, "description"),
        provenance=provenance, band=band or 1, expect=expect, checksum_sha256=checksum,
        allow_missing_nodata=allow_missing_nodata,
        id_property=_opt_str(raw, "id_property") or "unit_id",
        name_property=_opt_str(raw, "name_property") or "name",
        level_property=_opt_str(raw, "level_property") or "level",
        unit_id_prefix=_opt_str(raw, "unit_id_prefix") or "",
        extra=extra,
    )
    if spec.is_synthetic and "SYNTHETIC" not in spec.title.upper():
        raise ManifestError("synthetic datasets must say SYNTHETIC in their title", code="synthetic_not_marked")
    return spec


def _check_expect_numbers(expect: dict[str, Any]) -> None:
    for key in ("resolution", "bounds"):
        if key in expect:
            val = expect[key]
            n = 2 if key == "resolution" else 4
            if (not isinstance(val, list) or len(val) != n
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                               for v in val)):
                raise ManifestError(f"expect.{key} must be a list of {n} finite numbers", code="invalid_field")
