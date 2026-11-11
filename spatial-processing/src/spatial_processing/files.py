# Filesystem safety: resolving catalog asset paths strictly inside ASSET_ROOT, and the output
# directory under OUTPUT_ROOT (one UUID-named directory per job, atomic writes, safe TTL cleanup).

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .config import ApiError

log = logging.getLogger(__name__)

ALLOWED_ASSET_SUFFIXES = {".tif", ".tiff"}
RESULT_FILE = "result.json"
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


# Return True if `path` is `root` or inside it (both already real paths).
# Used by resolve_asset_path and OutputManager.resolve_result.
def _is_inside(root: Path, path: Path) -> bool:
    return os.path.commonpath([str(root), str(path)]) == str(root)


# Log the specific reason server-side and return a generic 500 for the client.
# Used by resolve_asset_path.
def _reject(reason: str) -> ApiError:
    log.warning("rejected catalog asset_path: %s", reason)
    return ApiError(500, "ASSET_PATH_REJECTED", "the dataset's stored asset location is not permitted")


# Map a catalog asset_path (relative to the asset root, or absolute inside it) to a real local
# GeoTIFF; rejects traversal, URLs, GDAL /vsi paths, symlink escapes and non-files even though the
# catalog is trusted. Used by service._open_selection.
def resolve_asset_path(asset_root: Path, asset_path: str | None) -> Path:
    if not asset_path or not isinstance(asset_path, str):
        raise _reject("empty asset_path")
    if "\x00" in asset_path or "\\" in asset_path:
        raise _reject("control or backslash characters")
    if _SCHEME_RE.match(asset_path) or asset_path.startswith("/vsi"):
        raise _reject("URL scheme or GDAL virtual filesystem")

    pure = PurePosixPath(asset_path)
    root = Path(os.path.realpath(asset_root))
    if pure.is_absolute():  # tolerated only when lexically inside the configured root
        try:
            pure = pure.relative_to(PurePosixPath(str(asset_root)))
        except ValueError:
            raise _reject("absolute path outside asset root") from None
    if not pure.parts or any(part in ("..", "") for part in pure.parts):
        raise _reject("parent traversal")
    if pure.suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
        raise _reject("asset is not a GeoTIFF (.tif/.tiff)")

    resolved = Path(os.path.realpath(root.joinpath(*pure.parts)))
    if not _is_inside(root, resolved):
        raise _reject("resolves outside asset root (symlink escape)")
    try:
        st = os.stat(resolved)
    except FileNotFoundError:
        raise ApiError(500, "ASSET_MISSING", "the dataset's raster file is missing on the server") from None
    if not stat.S_ISREG(st.st_mode):
        raise _reject("not a regular file")
    return resolved


# Reduce arbitrary text to a conservative filename component. Used by service.clip.
def safe_filename_component(text: str, max_len: int = 64) -> str:
    cleaned = _UNSAFE_FILENAME_RE.sub("-", text).strip(".-_")
    return (cleaned or "dataset")[:max_len]


# Return True if `name` is a canonical (lower-case, hyphenated) UUID. Used by OutputManager.cleanup.
def _is_canonical_uuid(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


# Per-job output directories under the output root. Cleanup only touches direct children whose
# names are canonical UUIDs, that are real directories (not symlinks) and older than the TTL;
# inside them it unlinks only non-directory entries and never follows symlinks.
# Used by service.ProcessingService and app.create_app (startup cleanup).
class OutputManager:
    # Resolve the root once; cleanup state starts empty.
    def __init__(self, root: Path):
        self.root = Path(os.path.realpath(root))
        self._last_cleanup = 0.0
        self._cleanup_lock = threading.Lock()

    # Create the output root if needed. Used at startup and by service.readiness.
    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # Path of a job's directory. Used by create_job_dir, read_result and service._fail.
    def job_dir(self, job_id: uuid.UUID) -> Path:
        return self.root / str(job_id)

    # Create a fresh job directory (never reused). Used by service._run.
    def create_job_dir(self, job_id: uuid.UUID) -> Path:
        path = self.job_dir(job_id)
        path.mkdir(mode=0o750, exist_ok=False)
        return path

    # Path relative to the root, as stored in jobs.result_path. Used by service._run/download_metadata.
    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # Resolve a stored relative result path to a regular file inside the root; 410 OUTPUT_EXPIRED
    # if it is gone or unsafe. Used by service._completed_clip/download_metadata.
    def resolve_result(self, result_path: str | None) -> Path:
        expired = ApiError(410, "OUTPUT_EXPIRED", "the job's output is no longer available")
        if not result_path or result_path.startswith("/") or ".." in Path(result_path).parts:
            raise expired
        candidate = Path(os.path.realpath(self.root / result_path))
        if not _is_inside(self.root, candidate):
            raise expired
        try:
            st = os.stat(candidate)
        except FileNotFoundError:
            raise expired from None
        if not stat.S_ISREG(st.st_mode):
            raise expired
        return candidate

    # Write JSON atomically (temp file + rename). Used by service._run (result.json) and service.clip (sidecar).
    def write_json(self, directory: Path, name: str, payload: dict[str, Any]) -> Path:
        target = directory / name
        tmp = directory / f".tmp-{uuid.uuid4().hex}.json"
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, target)
        return target

    # Read a job's result.json, or None if it is missing/corrupt. Used by service.job_view.
    def read_result(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        try:
            return json.loads((self.job_dir(job_id) / RESULT_FILE).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    # Remove expired job directories (skipping `active` jobs); returns the names removed.
    # Used at startup (app.create_app), by maybe_cleanup and tests.
    def cleanup(self, ttl_seconds: int, active: Iterable[uuid.UUID] = ()) -> list[str]:
        if not self.root.is_dir():
            return []
        now = time.time()
        active_names = {str(j) for j in active}
        removed: list[str] = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                if not _is_canonical_uuid(entry.name) or entry.name in active_names:
                    continue
                if now - entry.stat(follow_symlinks=False).st_mtime < ttl_seconds:
                    continue
                if self.remove_job_dir(Path(entry.path)):
                    removed.append(entry.name)
        if removed:
            log.info("output cleanup removed %d expired job directories", len(removed))
        return removed

    # Delete a flat job directory (unlinking symlinks, never their targets); leaves it alone and
    # returns False if it has a subdirectory. Used by cleanup and service._fail.
    @staticmethod
    def remove_job_dir(path: Path) -> bool:
        entries = list(os.scandir(path))
        if any(entry.is_dir(follow_symlinks=False) for entry in entries):
            log.warning("output cleanup: unexpected subdirectory in %s; leaving it", path.name)
            return False
        for entry in entries:
            os.unlink(entry.path)
        os.rmdir(path)
        return True

    # Run cleanup at most once per `interval_seconds` and never concurrently. Used by service._run.
    def maybe_cleanup(self, ttl_seconds: int, interval_seconds: int, active: Iterable[uuid.UUID] = ()) -> None:
        if not self._cleanup_lock.acquire(blocking=False):
            return
        try:
            if time.monotonic() - self._last_cleanup < interval_seconds:
                return
            self._last_cleanup = time.monotonic()
            try:
                self.cleanup(ttl_seconds, active)
            except OSError:
                log.exception("output cleanup failed")
        finally:
            self._cleanup_lock.release()
