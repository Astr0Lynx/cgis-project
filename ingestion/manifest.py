# Import manifest parsing (contract: README.md, "Ingestion contract").
# A manifest is one dataset object, a list of them, or {"datasets": [...]}. Relative `file` paths
# resolve against the manifest's directory; `expect` only asserts properties read from the file.

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


# One validated manifest entry. Built by parse_entry; consumed by raster, vector, catalog, importer.
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
    extra: dict[str, Any] = field(default_factory=dict)  # unknown manifest keys, kept in provenance

    # "<id>@v<version>" label for messages and log lines. Used across catalog, importer, raster.
    @property
    def key(self) -> str:
        return f"{self.dataset_id}@v{self.version}"

    # True when provenance or source marks the dataset synthetic. Used by parse_entry and importer.
    @property
    def is_synthetic(self) -> bool:
        source = self.source.lower()
        return bool(self.provenance.get("synthetic")) or "fixture" in source or "synthetic" in source


# Read a manifest file and return (base_dir, raw entries); entries are not validated yet.
# Used by cli._run_manifest and tests.
def load_manifest(path: str | Path) -> tuple[Path, list[dict[str, Any]]]:
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"manifest not found: {p}", code="manifest_not_found")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"manifest {p.name} is not valid JSON: {exc}", code="manifest_not_json") from exc

    if isinstance(doc, list):
        entries = doc
    elif isinstance(doc, dict):
        entries = doc["datasets"] if "datasets" in doc else [doc]
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


# Shorthand for an `invalid_field` ManifestError. Used by the field readers below and parse_entry.
def _invalid(message: str, key: str) -> ManifestError:
    return ManifestError(message, code="invalid_field", details={"field": key})


# Required non-empty string field. Used by parse_entry.
def _req_str(raw: dict, key: str) -> str:
    val = raw.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ManifestError(f"'{key}' is required and must be a non-empty string", code="missing_field",
                            details={"field": key})
    return val.strip()


# Optional string field; blank becomes None. Used by parse_entry.
def _opt_str(raw: dict, key: str) -> str | None:
    val = raw.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise _invalid(f"'{key}' must be a string", key)
    return val.strip() or None


# Optional integer field with inclusive bounds (booleans rejected). Used by parse_entry.
def _int(raw: dict, key: str, minimum: int | None = None, maximum: int | None = None) -> int | None:
    val = raw.get(key)
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        raise _invalid(f"'{key}' must be an integer", key)
    if (minimum is not None and val < minimum) or (maximum is not None and val > maximum):
        raise _invalid(f"'{key}'={val} is out of range", key)
    return val


# Optional JSON-object field, defaulting to {}. Used by parse_entry for provenance and expect.
def _obj(raw: dict, key: str) -> dict[str, Any]:
    val = raw.get(key)
    if val is None:
        return {}
    if not isinstance(val, dict):
        raise _invalid(f"'{key}' must be a JSON object", key)
    return val


# Validate one manifest entry into a DatasetSpec; raises ManifestError with a specific code.
# Used by cli._run_manifest and tests.
def parse_entry(raw: dict[str, Any], base_dir: Path) -> DatasetSpec:
    dataset_id = _req_str(raw, "dataset_id")
    if not SLUG_RE.match(dataset_id):
        raise ManifestError(f"dataset_id {dataset_id!r} must be a lowercase slug matching {SLUG_RE.pattern}",
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

    provenance = _obj(raw, "provenance")
    expect = _obj(raw, "expect")
    unknown_expect = set(expect) - EXPECT_KEYS
    if unknown_expect:
        raise _invalid(f"unknown 'expect' keys: {sorted(unknown_expect)}", "expect")
    for key, n in (("resolution", 2), ("bounds", 4)):
        val = expect.get(key)
        if key in expect and (not isinstance(val, list) or len(val) != n or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in val)):
            raise ManifestError(f"expect.{key} must be a list of {n} finite numbers", code="invalid_field")

    checksum = _opt_str(raw, "checksum_sha256")
    if checksum is not None:
        checksum = checksum.lower()
        if not SHA256_RE.match(checksum):
            raise _invalid("'checksum_sha256' must be 64 hex characters", "checksum_sha256")

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
