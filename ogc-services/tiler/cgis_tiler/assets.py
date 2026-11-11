# Catalog-backed, path-safe resolution of raster dataset_ids to local COG files: the only way the tile
# service turns a request into a path. The catalog asset_path is confined to the asset root after
# symlink resolution and must open with the GeoTIFF driver. Used by main.

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

LOGGER = logging.getLogger(__name__)

# Dataset ids appear in URLs; anything else is rejected before any database or filesystem access.
DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

ALLOWED_SUFFIXES = frozenset({".tif", ".tiff"})
ALLOWED_DRIVERS = frozenset({"GTiff", "COG"})


# Base error with a stable code and HTTP status; rendered as {"error": {...}} by main._asset_error.
class AssetError(Exception):
    status_code = 404
    code = "dataset_not_found"

    # Keeps the public message for the response body.
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# Unknown, malformed or non-ready dataset_id.
class DatasetNotFound(AssetError):
    pass


# The catalog row exists but its asset cannot be served safely (generic public message; reason is logged).
class AssetUnavailable(AssetError):
    code = "asset_unavailable"


# The catalog database cannot be queried. Raised by catalog.Catalog.
class CatalogUnavailable(AssetError):
    status_code = 503
    code = "catalog_unavailable"


# A ready raster row of catalog.current_datasets. Built by catalog.Catalog.
@dataclass(frozen=True)
class RasterRecord:
    dataset_id: str
    version: int
    title: str
    asset_path: str | None
    crs: str | None
    nodata: float | None
    units: str | None
    year: int | None
    bbox: tuple[float, float, float, float] | None
    # Public, non-path discovery metadata (served by main.list_datasets / describe_dataset).
    product_level: str | None = None
    resolution: tuple[float, float] | None = None
    public: dict | None = None      # group_id, synthetic, license, citation from provenance
    render: dict | None = None      # shared display scale (catalog.render_profile_for) or None


# Rejects ids that do not match DATASET_ID_RE. Used by AssetResolver.resolve and the unit tests.
def validate_dataset_id(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not DATASET_ID_RE.match(dataset_id):
        raise DatasetNotFound("Unknown dataset")
    return dataset_id


# Maps a catalog asset_path (relative, or absolute but inside the root) to a real .tif file inside
# asset_root; ValueError with an internal reason otherwise. Used by AssetResolver.resolve and the unit tests.
def confine_asset_path(asset_root: Path, asset_path: str | None) -> Path:
    if not asset_path or not isinstance(asset_path, str):
        raise ValueError("empty asset_path")
    if "\x00" in asset_path or "\\" in asset_path:
        raise ValueError("illegal character in asset_path")
    if "://" in asset_path or asset_path.startswith("/vsi"):
        raise ValueError("remote/virtual asset_path is not allowed")

    pure = PurePosixPath(asset_path)
    if ".." in pure.parts:
        raise ValueError("parent traversal in asset_path")
    if pure.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"asset suffix {pure.suffix!r} not allowed")

    root = asset_root.resolve(strict=True)
    candidate = Path(pure) if pure.is_absolute() else root / pure
    try:
        real = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as err:
        raise ValueError(f"asset file missing: {err}") from err
    if not real.is_relative_to(root):
        raise ValueError("asset_path escapes the asset root")
    if not real.is_file():
        raise ValueError("asset_path is not a regular file")
    return real


# Refuses files GDAL would not open as GeoTIFF/COG (e.g. a VRT disguised as .tif that references other
# files or URLs). Default driver check of AssetResolver; also used by the unit tests.
def check_driver(path: Path) -> None:
    import rasterio

    with rasterio.open(path) as src:
        if src.driver not in ALLOWED_DRIVERS:
            raise ValueError(f"driver {src.driver!r} not allowed")


# dataset_id -> (catalog record, confined local file path), with a short TTL cache. Used by main.
class AssetResolver:

    # lookup is the catalog query (catalog.Catalog.lookup_raster in main); driver_check is overridable for tests.
    def __init__(
        self,
        asset_root: Path,
        lookup: Callable[[str], RasterRecord | None],
        ttl_seconds: float = 30.0,
        driver_check: Callable[[Path], None] = check_driver,
    ):
        self.asset_root = asset_root
        self._lookup = lookup
        self._ttl = ttl_seconds
        self._driver_check = driver_check
        self._cache: dict[str, tuple[float, RasterRecord, Path]] = {}
        self._lock = threading.Lock()

    # Validates, looks up and confines a dataset; raises DatasetNotFound / AssetUnavailable.
    # Used by main.dataset_path and main.describe_dataset.
    def resolve(self, dataset_id: str) -> tuple[RasterRecord, Path]:
        validate_dataset_id(dataset_id)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(dataset_id)
            if hit and hit[0] > now:
                return hit[1], hit[2]

        record = self._lookup(dataset_id)
        if record is None:
            raise DatasetNotFound("Unknown dataset")
        try:
            path = confine_asset_path(self.asset_root, record.asset_path)
            self._driver_check(path)
        except Exception as err:  # noqa: BLE001 - every failure means "do not serve"
            LOGGER.warning("refusing asset for dataset %s: %s", dataset_id, err)
            raise AssetUnavailable("Dataset is registered but its asset cannot be served") from err

        with self._lock:
            self._cache[dataset_id] = (now + self._ttl, record, path)
        return record, path

    # Empties the cache. Used by the unit tests.
    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
