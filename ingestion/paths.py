"""Server-owned asset path layout and path-safety helpers.

Catalog ``asset_path`` values are always *relative* to the assets root (``/data/assets`` inside
Compose). They are derived from validated dataset slugs only, never from client input.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from .errors import AssetError

DEFAULT_ASSETS_DIR = "/data/assets"
DEFAULT_FIXTURES_DIR = "/data/fixtures"
STAGING_DIRNAME = ".staging"

_SUFFIX = {"raster": ".tif", "vector": ".geojson"}


def assets_root(override: str | os.PathLike | None = None) -> Path:
    """Resolve the assets root: explicit override > $INGESTION_ASSETS_DIR > /data/assets."""
    raw = override or os.environ.get("INGESTION_ASSETS_DIR") or DEFAULT_ASSETS_DIR
    return Path(raw).absolute()


def asset_relpath(kind: str, dataset_id: str, version: int) -> PurePosixPath:
    """Deterministic relative location, e.g. ``rasters/<id>/v1/<id>_v1.tif``."""
    if kind not in _SUFFIX:
        raise AssetError(f"no asset layout for kind {kind!r}", code="unsupported_kind")
    return PurePosixPath(f"{kind}s", dataset_id, f"v{version}", f"{dataset_id}_v{version}{_SUFFIX[kind]}")


def resolve_under(root: Path, rel: str | PurePosixPath) -> Path:
    """Join ``rel`` onto ``root`` refusing absolute paths, ``..`` and symlink escapes."""
    rel_p = PurePosixPath(str(rel))
    if rel_p.is_absolute() or not rel_p.parts or any(p in ("..", "") for p in rel_p.parts):
        raise AssetError(f"unsafe asset path {str(rel)!r}", code="unsafe_path")
    candidate = root.joinpath(*rel_p.parts)
    real_root = root.resolve()
    real = candidate.resolve()
    if real != real_root and real_root not in real.parents:
        raise AssetError(f"asset path {str(rel)!r} escapes the assets root", code="unsafe_path")
    return candidate


def sha256_file(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
