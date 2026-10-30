"""Catalog-backed, path-safe resolution of raster dataset IDs to local COG files.

This is the only way the tile service turns a request into a file path. Clients
never supply a path or URL: they supply a catalog ``dataset_id``, which is looked
up in ``catalog.current_datasets`` (ready rasters only). The stored ``asset_path``
is then confined to the asset root after symlink resolution, and the file must
open with the GeoTIFF driver.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

LOGGER = logging.getLogger(__name__)

# Collection/dataset slugs appear in URLs; anything else is rejected before any
# database or filesystem access.
DATASET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
DATASET_ID_RE = re.compile(DATASET_ID_PATTERN)

ALLOWED_SUFFIXES = frozenset({".tif", ".tiff"})
ALLOWED_DRIVERS = frozenset({"GTiff", "COG"})


class AssetError(Exception):
    """Base error with a stable code and HTTP status for API responses."""

    status_code = 404
    code = "dataset_not_found"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DatasetNotFound(AssetError):
    status_code = 404
    code = "dataset_not_found"


class AssetUnavailable(AssetError):
    """The catalog row exists but its asset cannot be served safely.

    The public message is deliberately generic; the reason is logged.
    """

    status_code = 404
    code = "asset_unavailable"


class CatalogUnavailable(AssetError):
    status_code = 503
    code = "catalog_unavailable"


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


def validate_dataset_id(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not DATASET_ID_RE.match(dataset_id):
        raise DatasetNotFound("Unknown dataset")
    return dataset_id


def confine_asset_path(asset_root: Path, asset_path: str | None) -> Path:
    """Map a catalog ``asset_path`` to a real file inside ``asset_root``.

    The contract stores paths relative to the asset root; an absolute path is
    tolerated only if it already lies inside the root. Raises ``ValueError`` with
    an internal reason on any violation.
    """
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


def check_driver(path: Path) -> None:
    """Refuse anything that GDAL would not open as a GeoTIFF (e.g. a VRT
    disguised with a .tif suffix, which could reference other files/URLs)."""
    import rasterio

    with rasterio.open(path) as src:
        if src.driver not in ALLOWED_DRIVERS:
            raise ValueError(f"driver {src.driver!r} not allowed")


class AssetResolver:
    """dataset_id -> confined local file path, with a short TTL cache."""

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

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
