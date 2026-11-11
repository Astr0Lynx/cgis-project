"""Deterministic SYNTHETIC test files (never real population data). Generated on demand into a temp dir."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

# A 100 x 80 grid of 0.0005 degree cells starting at (78.00 E, 18.00 N), near Hyderabad.
ORIGIN_4326 = (78.0, 18.0)
RES_4326 = 0.0005
NODATA = -9999.0


def raster_4326(path: Path, *, width: int = 100, height: int = 80, value_offset: float = 0.0) -> Path:
    data = (np.arange(width * height, dtype=np.float32).reshape(height, width) % 7 + 1 + value_offset).astype("float32")
    data[:5, :5] = NODATA  # a NoData corner
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(ORIGIN_4326[0], ORIGIN_4326[1], RES_4326, RES_4326),
                       nodata=NODATA, tiled=True, blockxsize=64, blockysize=64, compress="deflate") as dst:
        dst.write(data, 1)
    return path


def raster_utm(path: Path) -> Path:
    width, height = 60, 40
    data = np.full((height, width), 2, dtype="int32")
    data[0, :] = -1
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="int32",
                       crs="EPSG:32644", transform=from_origin(200000.0, 2000000.0, 100.0, 100.0), nodata=-1) as dst:
        dst.write(data, 1)
    return path


def raster_no_crs(path: Path) -> Path:
    with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=1, dtype="float32",
                       transform=from_origin(0, 10, 1, 1), nodata=0) as dst:
        dst.write(np.ones((10, 10), dtype="float32"), 1)
    return path


def raster_unknown_crs(path: Path) -> Path:
    crs = CRS.from_proj4("+proj=tmerc +lat_0=11.3 +lon_0=79.7 +k=0.99 +x_0=123456 +y_0=0 +ellps=WGS84 +units=m")
    with rasterio.open(path, "w", driver="GTiff", width=10, height=10, count=1, dtype="float32", crs=crs,
                       transform=from_origin(0, 1000, 100, 100), nodata=0) as dst:
        dst.write(np.ones((10, 10), dtype="float32"), 1)
    return path


def raster_sparse(path: Path, width: int, height: int) -> Path:
    """A tiny file declaring a huge grid (no tile data written): a decompression-bomb stand-in."""
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(70, 30, 1e-5, 1e-5), nodata=0, tiled=True,
                       blockxsize=512, blockysize=512, compress="deflate", sparse_ok=True, bigtiff="YES"):
        pass
    return path


def corrupt_tiff(path: Path, valid: Path) -> Path:
    data = valid.read_bytes()
    path.write_bytes(data[:4] + bytes(range(256)) * 40)
    return path


def truncated_tiff(path: Path, valid: Path) -> Path:
    data = valid.read_bytes()
    path.write_bytes(data[: len(data) // 3])
    return path


def _square(x0, y0, d):
    return [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]]


def vector_ok(path: Path) -> Path:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"unit_id": "town-a", "name": "SYNTHETIC Town A", "level": "town"},
         "geometry": {"type": "Polygon", "coordinates": _square(78.010, 17.980, 0.010)}},
        {"type": "Feature", "properties": {"unit_id": "town-b", "name": "SYNTHETIC Town B", "level": "town"},
         "geometry": {"type": "Polygon", "coordinates": _square(78.030, 17.950, 0.005)}},
    ]}
    path.write_text(json.dumps(fc))
    return path


def vector_invalid(path: Path) -> Path:
    bowtie = [[[78.0, 17.9], [78.1, 18.0], [78.1, 17.9], [78.0, 18.0], [78.0, 17.9]]]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"unit_id": "bad", "name": "SYNTHETIC bowtie"},
         "geometry": {"type": "Polygon", "coordinates": bowtie}}]}
    path.write_text(json.dumps(fc))
    return path


def vector_bad_crs(path: Path) -> Path:
    fc = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::999999"}},
          "features": [{"type": "Feature", "properties": {"unit_id": "x", "name": "SYNTHETIC x"},
                        "geometry": {"type": "Polygon", "coordinates": _square(1, 1, 1)}}]}
    path.write_text(json.dumps(fc))
    return path


def make_all(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    out = {
        "raster": raster_4326(root / "synthetic_pop_4326.tif"),
        "raster_v2": raster_4326(root / "synthetic_pop_4326_v2.tif", value_offset=10.0),
        "raster_utm": raster_utm(root / "synthetic_utm44.tif"),
        "no_crs": raster_no_crs(root / "no_crs.tif"),
        "unknown_crs": raster_unknown_crs(root / "unknown_crs.tif"),
        "vector": vector_ok(root / "synthetic_towns.geojson"),
        "vector_invalid": vector_invalid(root / "bowtie.geojson"),
        "vector_bad_crs": vector_bad_crs(root / "bad_crs.geojson"),
    }
    out["corrupt"] = corrupt_tiff(root / "corrupt.tif", out["raster"])
    out["truncated"] = truncated_tiff(root / "truncated.tif", out["raster"])
    return out


def raster_meta(dataset_id: str, version: int | None = 1, **extra) -> dict:
    meta = {"dataset_id": dataset_id, "title": f"SYNTHETIC {dataset_id}", "kind": "raster", "product_level": "other",
            "year": 2011, "units": "people_per_cell", "source": "synthetic test fixture (application-api tests)",
            "provenance": {"synthetic": True, "license": "CC0-1.0 (test data)", "group_id": "test-group"}}
    if version is not None:
        meta["version"] = version
    meta.update(extra)
    return meta


def vector_meta(dataset_id: str, version: int | None = 1, **extra) -> dict:
    meta = {"dataset_id": dataset_id, "title": f"SYNTHETIC {dataset_id}", "kind": "vector", "product_level": "other",
            "source": "synthetic test fixture (application-api tests)", "provenance": {"synthetic": True}}
    if version is not None:
        meta["version"] = version
    meta.update(extra)
    return meta
