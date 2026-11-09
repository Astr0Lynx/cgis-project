"""Build ingestion manifest v1 entries (compatible with ingestion/manifest.py) with explicit provenance."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import rasterio

from .fetch import sha256_file
from .sources import Source

SCHEMA_PRODUCT_LEVEL = "source"
ALLOWED_UNITS = {"people_per_cell", "m2_per_cell"}


def git_commit(repo: str | Path = ".") -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def raster_entry(src: Source, file_rel: str, abs_path: str | Path, *, processing_steps: list[str],
                 upstream_checksums: dict[str, str] | None = None, geographic_target: str = "India",
                 code_commit: str | None = None, version: int = 1, dataset_id: str | None = None) -> dict:
    if src.units not in ALLOWED_UNITS:
        raise ValueError(f"{src.units!r} is not a raster source unit")
    with rasterio.open(abs_path) as ds:
        epsg = ds.crs.to_epsg()
        if epsg is None:
            raise ValueError(f"{abs_path}: CRS has no EPSG code; the importer requires one (reproject or file A4 CR)")
        expect = {"crs": f"EPSG:{epsg}", "nodata": ds.nodata, "dtype": ds.dtypes[0],
                  "width": ds.width, "height": ds.height}
    return {
        "dataset_id": dataset_id or src.source_id, "version": version,
        "title": src.title, "description": src.semantics, "kind": "raster", "product_level": SCHEMA_PRODUCT_LEVEL,
        "year": src.year, "units": src.units, "source": src.edition, "file": file_rel, "band": 1,
        "checksum_sha256": sha256_file(abs_path), "expect": expect,
        "provenance": {
            "upstream_url": src.url, "upstream_license": src.license, "upstream_edition": src.edition,
            "upstream_checksums": upstream_checksums or {}, "geographic_target": geographic_target,
            "processing_steps": processing_steps, "access_state": src.access,
            "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "phase1_commit": code_commit or git_commit(), "model": "none (source preparation only)",
            "level": "source",
        },
    }


def write_manifest(path: str | Path, entries: list[dict]) -> None:
    ids = [(e["dataset_id"], e["version"]) for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate (dataset_id, version) in manifest")
    Path(path).write_text(json.dumps({"manifest_version": 1, "datasets": entries}, indent=2) + "\n")
