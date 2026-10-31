"""Write deliberately bad import inputs for the negative ingestion tests (SYNTHETIC, test-only).

    python make_negative_fixtures.py /data/fixtures/e2e/neg

Each case is a manifest plus its file(s). run_e2e.sh imports every manifest with the real
ingestion CLI and records the exit code and output. test_50_negative.py then asserts that each
import failed clearly and that the ready datasets were not touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

NOTICE = "SYNTHETIC negative test input (tests/e2e); not real data"


def write_raster(path: Path, value: float, crs) -> None:
    data = np.full((8, 8), value, dtype="float32")
    profile = {"driver": "GTiff", "width": 8, "height": 8, "count": 1, "dtype": "float32",
               "transform": from_origin(78.0, 18.0, 0.001, 0.001), "nodata": -9999.0}
    if crs is not None:
        profile["crs"] = crs
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def manifest(dataset_id: str, kind: str, file: str, **extra) -> dict:
    entry = {"dataset_id": dataset_id, "version": 1, "title": f"SYNTHETIC e2e negative case {dataset_id}",
             "kind": kind, "product_level": "other", "source": "deterministic-fixture",
             "file": file, "provenance": {"synthetic": True, "notice": NOTICE}}
    if kind == "raster":
        entry.update(units="people_per_cell", year=2001)
    entry.update(extra)
    return entry


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cases: dict[str, dict] = {}

    # 1. Corrupted TIFF: valid TIFF magic bytes followed by garbage.
    (out / "corrupt.tif").write_bytes(b"II*\x00" + bytes(range(256)) * 4)
    cases["corrupt_tiff"] = manifest("e2e-neg-corrupt-tiff", "raster", "corrupt.tif")

    # 2. Raster without any CRS.
    write_raster(out / "no_crs.tif", 1.0, None)
    cases["missing_crs"] = manifest("e2e-neg-missing-crs", "raster", "no_crs.tif")

    # 3. Raster in a CRS with no EPSG code (custom transverse Mercator).
    write_raster(out / "custom_crs.tif", 1.0,
                 CRS.from_proj4("+proj=tmerc +lat_0=0 +lon_0=77.7 +k=0.9996 +x_0=0 +y_0=0 +ellps=WGS84 +units=m"))
    cases["unsupported_crs"] = manifest("e2e-neg-unsupported-crs", "raster", "custom_crs.tif")

    # 4. Conflict: same id/version as a ready fixture, different content.
    write_raster(out / "conflict_2001.tif", 3.0, "EPSG:4326")
    cases["conflicting_reimport"] = manifest("synthetic-worldpop-2001", "raster", "conflict_2001.tif",
                                             title="SYNTHETIC population raster (2001)", product_level="source")

    # 5. Invalid (self-intersecting "bowtie") polygon.
    bowtie = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "e2e-bowtie",
        "properties": {"unit_id": "e2e-bowtie", "name": "SYNTHETIC bowtie", "level": "synthetic_test",
                       "source": "deterministic-fixture"},
        "geometry": {"type": "Polygon", "coordinates": [[[78.01, 17.98], [78.02, 17.99], [78.02, 17.98],
                                                          [78.01, 17.99], [78.01, 17.98]]]}}]}
    (out / "bowtie.geojson").write_text(json.dumps(bowtie))
    cases["invalid_geometry"] = manifest("e2e-neg-invalid-geometry", "vector", "bowtie.geojson")

    # 6. Manifest pointing at a file that does not exist.
    cases["missing_file"] = manifest("e2e-neg-missing-file", "raster", "does_not_exist.tif")

    for name, entry in cases.items():
        (out / f"{name}.json").write_text(json.dumps(entry, indent=2))
    (out / "cases.json").write_text(json.dumps(sorted(cases), indent=2))
    print(json.dumps({"written": sorted(cases), "dir": str(out)}))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/data/fixtures/e2e/neg"))
