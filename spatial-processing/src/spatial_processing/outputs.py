"""Controlled output directory: one UUID-named directory per job under the output root.

Cleanup is deliberately narrow: it only touches direct children of the output root
whose names are canonical UUIDs, that are real directories (not symlinks), and that are
older than the TTL; inside them it unlinks only non-directory entries and never
follows symlinks. Nothing outside the output root can be deleted.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from . import errors
from .errors import ApiError

log = logging.getLogger(__name__)

RESULT_FILE = "result.json"
TMP_PREFIX = ".tmp-"


def _is_canonical_uuid(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


class OutputManager:
    def __init__(self, root: Path):
        self.root = Path(os.path.realpath(root))
        self._last_cleanup = 0.0
        self._cleanup_lock = threading.Lock()

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def create_job_dir(self, job_id: uuid.UUID) -> Path:
        path = self.root / str(job_id)
        path.mkdir(mode=0o750, exist_ok=False)  # UUIDs are unique: never reuse a directory
        return path

    def job_dir(self, job_id: uuid.UUID) -> Path:
        return self.root / str(job_id)

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def resolve_result(self, result_path: str | None) -> Path:
        """Resolve a stored relative result path; 410 if it is gone or unsafe."""
        if not result_path or result_path.startswith("/") or ".." in Path(result_path).parts:
            raise ApiError(410, errors.OUTPUT_EXPIRED, "the job's output is no longer available")
        candidate = Path(os.path.realpath(self.root / result_path))
        if os.path.commonpath([str(self.root), str(candidate)]) != str(self.root):
            raise ApiError(410, errors.OUTPUT_EXPIRED, "the job's output is no longer available")
        try:
            st = os.stat(candidate)
        except FileNotFoundError:
            raise ApiError(410, errors.OUTPUT_EXPIRED, "the job's output is no longer available") from None
        if not stat.S_ISREG(st.st_mode):
            raise ApiError(410, errors.OUTPUT_EXPIRED, "the job's output is no longer available")
        return candidate

    def write_json(self, directory: Path, name: str, payload: dict[str, Any]) -> Path:
        target = directory / name
        tmp = directory / f"{TMP_PREFIX}{uuid.uuid4().hex}.json"
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, target)
        return target

    def read_result(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        path = self.job_dir(job_id) / RESULT_FILE
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def cleanup(self, ttl_seconds: int, active: Iterable[uuid.UUID] = ()) -> list[str]:
        """Remove expired job directories; returns the names removed."""
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

    @staticmethod
    def remove_job_dir(path: Path) -> bool:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    log.warning("output cleanup: unexpected subdirectory in %s; leaving it", path.name)
                    return False
        with os.scandir(path) as entries:
            for entry in entries:
                os.unlink(entry.path)  # removes symlinks themselves, never their targets
        os.rmdir(path)
        return True

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
