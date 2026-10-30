"""Deterministic SYNTHETIC fixture generator — implements ``contracts/SYNTHETIC_DATA.md``.

THESE FILES ARE NOT REAL POPULATION DATA. They are not WorldPop products and not scientific estimates;
the years are metadata labels only. Every output says SYNTHETIC / deterministic-fixture.

Outputs (in ``out_dir``):

* ``synthetic_2001.tif``, ``synthetic_2011.tif`` — 64×64 float32, EPSG:4326, UL (78.000, 18.000),
  pixel (+0.001, -0.001); NoData -9999 on the first 4 rows and first 4 columns; valid cells 1 / 2.
* ``synthetic_towns.geojson`` — Town A and Town B polygons.
* ``synthetic_test_aois.geojson`` — NoData-overlap and disjoint AOIs for negative tests (not imported).
* ``expected_values.json`` — analytical pixel-center (``all_touched=false``) answers.
* ``manifest.json`` — import manifest for the three fixture datasets (relative file paths).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_origin

GENERATOR_VERSION = "1"
SPEC = "contracts/SYNTHETIC_DATA.md"
NOTICE = ("SYNTHETIC deterministic fixture for CyberGIS Phase 2 platform testing. NOT real population data, "
          "NOT a WorldPop product, NOT a scientific estimate; years are labels only.")
SOURCE = "deterministic-fixture"

WIDTH = HEIGHT = 64
ORIGIN_X, ORIGIN_Y = 78.000, 18.000
PIXEL_SIZE = 0.001
CRS = "EPSG:4326"
NODATA = -9999.0
NODATA_ROWS = NODATA_COLS = 4
UNITS = "people_per_cell"

RASTERS: tuple[dict[str, Any], ...] = (
    {"dataset_id": "synthetic-worldpop-2001", "year": 2001, "value": 1.0, "file": "synthetic_2001.tif"},
    {"dataset_id": "synthetic-worldpop-2011", "year": 2011, "value": 2.0, "file": "synthetic_2011.tif"},
)
VECTOR_ID = "synthetic-towns"
VECTOR_FILE = "synthetic_towns.geojson"
AOI_FILE = "synthetic_test_aois.geojson"
EXPECTED_FILE = "expected_values.json"
MANIFEST_FILE = "manifest.json"

# (unit_id, name, (west, south, east, north))
TOWNS = (
    ("town-a", "Town A", (78.010, 17.980, 78.020, 17.990)),
    ("town-b", "Town B", (78.030, 17.950, 78.035, 17.955)),
)
TEST_AOIS = (
    ("aoi-nodata-overlap", "SYNTHETIC AOI overlapping the NoData strip", (78.000, 17.990, 78.010, 18.000)),
    ("aoi-disjoint", "SYNTHETIC AOI disjoint from the raster", (79.000, 19.000, 79.010, 19.010)),
)


def transform():
    return from_origin(ORIGIN_X, ORIGIN_Y, PIXEL_SIZE, PIXEL_SIZE)


def build_array(value: float) -> np.ndarray:
    arr = np.full((HEIGHT, WIDTH), value, dtype=np.float32)
    arr[:NODATA_ROWS, :] = NODATA
    arr[:, :NODATA_COLS] = NODATA
    return arr


def rectangle(bounds: tuple[float, float, float, float]) -> dict[str, Any]:
    w, s, e, n = bounds
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _cells(bounds: tuple[float, float, float, float]) -> tuple[slice, slice]:
    """Pixel-center selection of the grid for an axis-aligned rectangle (analytical, no rasterization)."""
    w, s, e, n = bounds
    cols = [c for c in range(WIDTH) if w < ORIGIN_X + (c + 0.5) * PIXEL_SIZE < e]
    rows = [r for r in range(HEIGHT) if s < ORIGIN_Y - (r + 0.5) * PIXEL_SIZE < n]
    if not cols or not rows:
        return slice(0, 0), slice(0, 0)
    return slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)


def expected_values() -> dict[str, Any]:
    arrays = {r["year"]: build_array(r["value"]) for r in RASTERS}
    aois: dict[str, Any] = {}
    for aoi_id, _name, bounds in TOWNS + TEST_AOIS:
        rs, cs = _cells(bounds)
        entry: dict[str, Any] = {"bounds": list(bounds), "cells_total": 0, "valid_pixels": 0, "nodata_pixels": 0}
        block = {y: a[rs, cs] for y, a in arrays.items()}
        cells = next(iter(block.values())).size
        if cells == 0:
            entry["expect"] = "no_overlap_error"
        else:
            valid = int((block[2001] != NODATA).sum())
            entry.update(cells_total=int(cells), valid_pixels=valid, nodata_pixels=int(cells) - valid,
                         rows=[rs.start, rs.stop], cols=[cs.start, cs.stop])
            for r in RASTERS:
                b = block[r["year"]]
                v = b[b != NODATA]
                entry.setdefault("sum", {})[str(r["year"])] = int(v.sum())
                entry.setdefault("mean", {})[str(r["year"])] = float(v.mean()) if v.size else None
        aois[aoi_id] = entry
    whole = {str(r["year"]): {"valid_pixels": int((a != NODATA).sum()),
                              "nodata_pixels": int((a == NODATA).sum()), "sum": int(a[a != NODATA].sum())}
             for r, a in zip(RASTERS, arrays.values())}
    return {
        "synthetic": True, "notice": NOTICE, "spec": SPEC, "generator_version": GENERATOR_VERSION,
        "pixel_inclusion": "pixel_center (all_touched=false), no resampling",
        "grid": {"width": WIDTH, "height": HEIGHT, "crs": CRS, "transform": list(transform())[:6],
                 "bounds": [ORIGIN_X, ORIGIN_Y - HEIGHT * PIXEL_SIZE, ORIGIN_X + WIDTH * PIXEL_SIZE, ORIGIN_Y],
                 "nodata": NODATA, "nodata_rows": NODATA_ROWS, "nodata_cols": NODATA_COLS, "units": UNITS},
        "whole_raster": whole,
        "aois": aois,
    }


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write_raster(path: Path, spec: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    profile = {"driver": "GTiff", "width": WIDTH, "height": HEIGHT, "count": 1, "dtype": "float32", "crs": CRS,
               "transform": transform(), "nodata": NODATA, "compress": "deflate"}
    try:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(build_array(spec["value"]), 1)
            dst.update_tags(SYNTHETIC="TRUE", NOTICE=NOTICE, DATASET_ID=spec["dataset_id"],
                            YEAR_LABEL=str(spec["year"]), UNITS=UNITS, SOURCE=SOURCE,
                            GENERATOR=f"ingestion.fixtures v{GENERATOR_VERSION}")
            dst.set_band_unit(1, UNITS)
            dst.set_band_description(1, f"SYNTHETIC {UNITS} ({spec['year']} label)")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def towns_geojson() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": VECTOR_ID,
        "synthetic": True,
        "notice": NOTICE,
        "features": [
            {"type": "Feature", "id": uid,
             "properties": {"unit_id": uid, "name": name, "level": "synthetic_town", "source": SOURCE,
                            "synthetic": True},
             "geometry": rectangle(bounds)}
            for uid, name, bounds in TOWNS
        ],
    }


def aoi_geojson(expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "synthetic-test-aois",
        "synthetic": True,
        "notice": NOTICE + " Test AOIs only; not imported.",
        "features": [
            {"type": "Feature", "id": aid,
             "properties": {"aoi_id": aid, "name": name, "source": SOURCE, "synthetic": True,
                            "expected": expected["aois"][aid]},
             "geometry": rectangle(bounds)}
            for aid, name, bounds in TEST_AOIS
        ],
    }


def _provenance(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    p = {"synthetic": True, "notice": NOTICE, "generator": "ingestion.fixtures",
         "generator_version": GENERATOR_VERSION, "spec": SPEC}
    p.update(extra or {})
    return p


def manifest() -> dict[str, Any]:
    datasets: list[dict[str, Any]] = []
    for r in RASTERS:
        datasets.append({
            "dataset_id": r["dataset_id"], "version": 1,
            "title": f"SYNTHETIC population raster ({r['year']})",
            "description": f"{NOTICE} Every valid cell = {int(r['value'])}.",
            "kind": "raster", "product_level": "source", "year": r["year"], "units": UNITS, "source": SOURCE,
            "file": r["file"],
            "expect": {"crs": CRS, "width": WIDTH, "height": HEIGHT, "band_count": 1, "dtype": "float32",
                       "nodata": NODATA, "resolution": [PIXEL_SIZE, PIXEL_SIZE],
                       "bounds": [ORIGIN_X, ORIGIN_Y - HEIGHT * PIXEL_SIZE, ORIGIN_X + WIDTH * PIXEL_SIZE,
                                  ORIGIN_Y]},
            "provenance": _provenance({"year_is_label_only": True}),
        })
    datasets.append({
        "dataset_id": VECTOR_ID, "version": 1, "title": "SYNTHETIC towns (Town A, Town B)",
        "description": f"{NOTICE} Rectangles aligned to the synthetic raster grid.",
        "kind": "vector", "product_level": "source", "year": None, "units": None, "source": SOURCE,
        "file": VECTOR_FILE, "expect": {"feature_count": len(TOWNS)},
        "provenance": _provenance(),
    })
    return {"manifest_version": 1, "synthetic": True, "notice": NOTICE, "datasets": datasets}


def generate(out_dir: str | Path) -> dict[str, Path]:
    """Write all fixtures into ``out_dir``. Re-running yields semantically identical output."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for r in RASTERS:
        p = out / r["file"]
        _write_raster(p, r)
        written[r["dataset_id"]] = p
    exp = expected_values()
    for name, obj in ((VECTOR_FILE, towns_geojson()), (AOI_FILE, aoi_geojson(exp)),
                      (EXPECTED_FILE, exp), (MANIFEST_FILE, manifest())):
        p = out / name
        _atomic_write_bytes(p, _json_bytes(obj))
        written[name] = p
    return written
