# Server-owned storage roots (upload staging, export outputs) and asset path checks. File names are
# server-generated; files are created O_EXCL|O_NOFOLLOW under an O_DIRECTORY root handle, and paths read
# back from the database are re-validated before use.

from __future__ import annotations

import os
import re
import shutil
import stat
import unicodedata
from pathlib import Path, PurePosixPath

STAGED_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(tif|geojson)$")
RESULT_PATH_RE = re.compile(r"^[0-9a-f]{32}/[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")  # classic TIFF and BigTIFF, both byte orders
RASTER_EXTENSIONS = {".tif", ".tiff"}
VECTOR_EXTENSIONS = {".geojson", ".json"}
EXT_FOR_KIND = {"raster": "tif", "vector": "geojson"}


# Unsafe or missing file with a stable code. Mapped to HTTP/job errors by callers.
class StorageError(Exception):
    # Store code and message.
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# Create a storage root and return its real path; refuse a symlink. Used by the app lifespan.
def ensure_root(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise StorageError("UNSAFE_ROOT", "storage root must not be a symlink")
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode):
        raise StorageError("UNSAFE_ROOT", "storage root is not a directory")
    return Path(os.path.realpath(root))


# O_DIRECTORY|O_NOFOLLOW handle of a root. Used by create_exclusive.
def _root_fd(root: Path) -> int:
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


# Open root/name for writing, failing if it exists or is a symlink. Used by upload_stream.
def create_exclusive(root: Path, name: str, mode: int = 0o640) -> int:
    if "/" in name or name in ("", ".", "..") or "\x00" in name:
        raise StorageError("UNSAFE_NAME", "invalid server file name")
    dfd = _root_fd(root)
    try:
        return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                       mode, dir_fd=dfd)
    finally:
        os.close(dfd)


# Regular file directly inside the staging root for a stored staged name. Used by the ingest worker.
def staged_path(root: Path, staged_name: str) -> Path:
    if not STAGED_NAME_RE.match(staged_name or ""):
        raise StorageError("UNSAFE_NAME", "invalid staged file name")
    real_root = Path(os.path.realpath(root))
    path = real_root / staged_name
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise StorageError("STAGED_FILE_MISSING", "the staged upload file is missing") from None
    if not stat.S_ISREG(st.st_mode):
        raise StorageError("UNSAFE_NAME", "staged upload is not a regular file")
    return path


# Delete a staged upload if its name is valid. Used by routes_admin, upload_stream and the ingest worker.
def remove_staged(root: Path, staged_name: str) -> bool:
    try:
        path = staged_path(root, staged_name)
    except StorageError:
        return False
    path.unlink(missing_ok=True)
    return True


# Validate a 32-hex job directory name. Used by make_result_dir/remove_result_dir.
def result_dir_name(job_hex: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", job_hex):
        raise StorageError("UNSAFE_NAME", "invalid job directory")
    return job_hex


# Create the per-job export directory. Used by worker.export.handle.
def make_result_dir(root: Path, job_hex: str) -> Path:
    real_root = Path(os.path.realpath(root))
    path = real_root / result_dir_name(job_hex)
    path.mkdir(mode=0o750, exist_ok=True)
    if os.path.islink(path) or not path.is_dir():
        raise StorageError("UNSAFE_NAME", "export directory is not a plain directory")
    return path


# Regular export file for a stored '<job hex>/<file>' path inside the export root. Used by downloads.
def resolve_result(root: Path, result_path: str) -> Path:
    if not RESULT_PATH_RE.match(result_path or ""):
        raise StorageError("UNSAFE_NAME", "invalid export path")
    real_root = Path(os.path.realpath(root))
    candidate = Path(os.path.realpath(real_root / result_path))
    if os.path.commonpath([str(real_root), str(candidate)]) != str(real_root):
        raise StorageError("UNSAFE_NAME", "export path escapes the export root")
    try:
        st = os.stat(candidate)
    except FileNotFoundError:
        raise StorageError("EXPORT_MISSING", "the export file no longer exists") from None
    if not stat.S_ISREG(st.st_mode):
        raise StorageError("UNSAFE_NAME", "export is not a regular file")
    return candidate


# Delete one export directory (files only, never follows symlinks). Used by the export worker.
def remove_result_dir(root: Path, job_hex: str) -> None:
    real_root = Path(os.path.realpath(root))
    path = real_root / result_dir_name(job_hex)
    if os.path.islink(path) or not path.is_dir():
        return
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                continue
            os.unlink(entry.path)
    try:
        os.rmdir(path)
    except OSError:
        pass


# Map a catalog asset_path to a regular file inside the asset root (no schemes, '..', symlink escapes,
# wrong extensions). Used by routes_exports and worker.export.
def resolve_asset(asset_root: Path, asset_path: str, kind: str) -> Path:
    if not asset_path or "\x00" in asset_path or "\\" in asset_path or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:",
                                                                            asset_path):
        raise StorageError("ASSET_PATH_REJECTED", "the dataset's stored asset location is not permitted")
    pure = PurePosixPath(asset_path)
    if pure.is_absolute() or not pure.parts or any(p in ("..", "", ".") for p in pure.parts):
        raise StorageError("ASSET_PATH_REJECTED", "the dataset's stored asset location is not permitted")
    allowed = RASTER_EXTENSIONS if kind == "raster" else VECTOR_EXTENSIONS
    if pure.suffix.lower() not in allowed:
        raise StorageError("ASSET_PATH_REJECTED", "the dataset's stored asset has an unexpected type")
    real_root = Path(os.path.realpath(asset_root))
    resolved = Path(os.path.realpath(real_root.joinpath(*pure.parts)))
    if os.path.commonpath([str(real_root), str(resolved)]) != str(real_root):
        raise StorageError("ASSET_PATH_REJECTED", "the dataset's stored asset location is not permitted")
    try:
        st = os.stat(resolved)
    except FileNotFoundError:
        raise StorageError("ASSET_MISSING", "the dataset's file is missing on the server") from None
    if not stat.S_ISREG(st.st_mode):
        raise StorageError("ASSET_PATH_REJECTED", "the dataset's stored asset is not a regular file")
    return resolved


# Free bytes on the volume of a root. Used by upload_stream and the export worker.
def free_bytes(root: Path) -> int:
    return shutil.disk_usage(root).free


# Total size of the files directly in a root. Used by upload_stream.preflight (staging quota).
def used_bytes(root: Path) -> int:
    total = 0
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
    return total


# 'raster' for TIFF/BigTIFF magic, 'vector' for a JSON object, else None. Used by upload_stream.
def sniff_kind(head: bytes) -> str | None:
    if head[:4] in TIFF_MAGIC:
        return "raster"
    text = head.lstrip(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
    if text[:1] == b"{":
        return "vector"
    return None


# Kind implied by a file name extension. Used by upload_stream to match name and content.
def extension_kind(filename: str | None) -> str | None:
    if not filename:
        return None
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in RASTER_EXTENSIONS:
        return "raster"
    if suffix in VECTOR_EXTENSIONS:
        return "vector"
    return None


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._()+-]+")


# Client file name reduced to a display-only basename (never a path). Used by upload_stream.
def display_filename(raw: str | None) -> str | None:
    if raw is None:
        return None
    name = unicodedata.normalize("NFKC", str(raw)).replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")
    return name[:255] or None
