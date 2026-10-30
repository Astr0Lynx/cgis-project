"""Path safety for catalog asset paths and generated outputs.

Even though asset paths come from the trusted catalog, they are re-validated here so
that a bad catalog row cannot make the service read outside the asset root, follow a
symlink out of it, or hand GDAL a remote/virtual filesystem URL.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path, PurePosixPath

from . import errors
from .errors import ApiError

ALLOWED_ASSET_SUFFIXES = {".tif", ".tiff"}
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


log = logging.getLogger(__name__)


def _reject(reason: str) -> ApiError:
    # The specific reason is logged server-side only; clients get a generic message.
    log.warning("rejected catalog asset_path: %s", reason)
    return ApiError(500, errors.ASSET_PATH_REJECTED, "the dataset's stored asset location is not permitted")


def resolve_asset_path(asset_root: Path, asset_path: str | None) -> Path:
    """Map a catalog ``asset_path`` (relative to the asset root) to a real local file."""
    if not asset_path or not isinstance(asset_path, str):
        raise _reject("empty asset_path")
    if "\x00" in asset_path or "\\" in asset_path:
        raise _reject("control or backslash characters")
    if _SCHEME_RE.match(asset_path) or asset_path.startswith("/vsi"):
        raise _reject("URL scheme or GDAL virtual filesystem")

    # Contract: asset_path is relative to /data/assets. Tolerate a stored absolute path only
    # when it is lexically inside the configured root (e.g. "/data/assets/x.tif").
    pure = PurePosixPath(asset_path)
    root = Path(os.path.realpath(asset_root))
    if pure.is_absolute():
        try:
            pure = pure.relative_to(PurePosixPath(str(asset_root)))
        except ValueError:
            raise _reject("absolute path outside asset root") from None
    if any(part in ("..", "") for part in pure.parts) or not pure.parts:
        raise _reject("parent traversal")
    if pure.suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
        raise _reject("asset is not a GeoTIFF (.tif/.tiff)")

    resolved = Path(os.path.realpath(root.joinpath(*pure.parts)))
    if os.path.commonpath([str(root), str(resolved)]) != str(root):
        raise _reject("resolves outside asset root (symlink escape)")
    try:
        st = os.stat(resolved)
    except FileNotFoundError:
        raise ApiError(500, errors.ASSET_MISSING, "the dataset's raster file is missing on the server") from None
    if not stat.S_ISREG(st.st_mode):
        raise _reject("not a regular file")
    return resolved


_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename_component(text: str, max_len: int = 64) -> str:
    """Reduce arbitrary text to a conservative filename component."""
    cleaned = _SAFE_COMPONENT_RE.sub("-", text).strip(".-_")
    return (cleaned or "dataset")[:max_len]
