"""Idempotent, resumable, checksum-verified downloads for UNRESTRICTED public sources only.

Policy: this module never logs in, never follows portal forms and refuses any source whose registry
access state is not `available`. State (size, sha256) is recorded in a JSON sidecar, not in code.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable

import requests

from .sources import AVAILABLE, Source

CHUNK = 1024 * 1024
UA = "cgis-agent01-fetch/0.1 (academic research; contact via repo owner)"


class FetchError(RuntimeError):
    pass


class RestrictedSourceError(FetchError):
    pass


class ChecksumMismatch(FetchError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def _locked_state(state_path: Path):
    """Exclusive lock around read-modify-write of the shared JSON state (several fetches may run at once)."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path.with_name(state_path.name + ".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def write_state_atomic(state_path: Path, state: dict) -> None:
    tmp = state_path.with_name(state_path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, state_path)


def read_state(state_path: Path, retries: int = 5) -> dict:
    for i in range(retries):
        try:
            return json.loads(Path(state_path).read_text()) if Path(state_path).exists() else {}
        except json.JSONDecodeError:
            time.sleep(0.2 * (i + 1))      # a non-atomic writer may be mid-write
    raise FetchError(f"{state_path} unreadable after {retries} tries")


def assert_fetchable(src: Source) -> None:
    if src.access != AVAILABLE:
        raise RestrictedSourceError(
            f"{src.source_id} has access state {src.access!r}; scripted download refused (see BLOCKER_REQUEST.md)")


def head_size(url: str, session: requests.Session | None = None, timeout: int = 30) -> int | None:
    s = session or requests
    r = s.head(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": UA})
    r.raise_for_status()
    n = r.headers.get("Content-Length")
    return int(n) if n is not None else None


def download_resumable(url: str, dest: str | Path, *, expected_sha256: str | None = None,
                       expected_size: int | None = None, session: requests.Session | None = None,
                       max_retries: int = 8, timeout: int = 60,
                       progress: Callable[[int, int | None], None] | None = None) -> dict:
    """Download `url` to `dest` via `dest.part`, resuming with HTTP Range when the server honours it.

    Idempotent: if `dest` exists and verifies (size, then sha256 when given) nothing is transferred.
    Returns {"path","size","sha256","resumed_from","skipped"}.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    sess = session or requests.Session()

    if dest.exists():
        ok_size = expected_size is None or dest.stat().st_size == expected_size
        if ok_size:
            digest = sha256_file(dest)
            if expected_sha256 is None or digest == expected_sha256:
                return {"path": str(dest), "size": dest.stat().st_size, "sha256": digest,
                        "resumed_from": 0, "skipped": True}
            if expected_sha256 is not None:
                raise ChecksumMismatch(f"{dest}: sha256 {digest} != expected {expected_sha256}")
        dest.unlink()  # wrong size -> redownload

    explicit_size = expected_size is not None
    # HEAD Content-Length is only a HINT (some hosts, e.g. raw.githubusercontent.com, report a different length than
    # the body). A GET that completes without a transport error is authoritative; only an explicit expected_size is enforced.
    total = expected_size if explicit_size else head_size(url, sess, timeout)
    resumed_from = part.stat().st_size if part.exists() else 0
    attempt = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if total is not None and have == total:
            break
        if total is not None and have > total:
            part.unlink()
            have = 0
            if attempt >= 1 and not explicit_size:     # fresh body already larger than the HEAD hint: trust the body
                total = None
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with sess.get(url, stream=True, headers=headers, timeout=timeout) as r:
                if r.status_code == 200 and have:      # server ignored Range -> restart cleanly
                    have = 0
                    part.unlink()
                elif r.status_code not in (200, 206):
                    r.raise_for_status()
                mode = "ab" if (have and r.status_code == 206) else "wb"
                with open(part, mode) as f:
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
                        if progress:
                            progress(f.tell(), total)
            break                                       # stream ended without a transport error -> transfer complete
        except (requests.RequestException, OSError) as exc:
            attempt += 1
            if attempt > max_retries:
                raise FetchError(f"giving up on {url}: {exc}") from exc
            time.sleep(min(2 ** attempt, 60))
    size = part.stat().st_size
    if explicit_size and size != expected_size:
        raise FetchError(f"{url}: size {size} != expected {expected_size}")
    digest = sha256_file(part)
    if expected_sha256 is not None and digest != expected_sha256:
        part.unlink()
        raise ChecksumMismatch(f"{url}: sha256 {digest} != expected {expected_sha256}")
    os.replace(part, dest)
    return {"path": str(dest), "size": size, "sha256": digest, "resumed_from": resumed_from, "skipped": False}


def fetch_source(src: Source, root: str | Path, state_path: str | Path, **kw) -> dict:
    """Fetch one registry source into `<root>/incoming/<source_id>/` and record state. Refuses gated sources."""
    assert_fetchable(src)
    state_path = Path(state_path)
    state = read_state(state_path)
    prev = state.get(src.source_id, {})
    fname = src.url.rsplit("/", 1)[-1]
    dest = Path(root) / "incoming" / src.source_id / fname
    res = download_resumable(src.url, dest, expected_sha256=prev.get("sha256"),
                             expected_size=prev.get("size_bytes"), **kw)
    companions = {}
    for cu in (src.extra or {}).get("companion_urls", []):            # e.g. shapefile .dbf/.shx/.prj next to the .shp
        cr = download_resumable(cu, dest.parent / cu.rsplit("/", 1)[-1], **kw)
        companions[cu.rsplit("/", 1)[-1]] = {"size_bytes": cr["size"], "sha256": cr["sha256"]}
    with _locked_state(state_path):
        state = read_state(state_path)   # re-read under the lock
        cur = state.get(src.source_id, {})
        state[src.source_id] = {**cur, **prev, "size_bytes": res["size"], "sha256": res["sha256"],
                                "local_path": str(dest), "access": AVAILABLE,
                                **({"companions": companions} if companions else {}),
                                "retrieved_utc": prev.get("retrieved_utc") or cur.get("retrieved_utc")
                                or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        write_state_atomic(state_path, state)
    return res


def _merge_tiles(state_path: Path, source_id: str, tdone: dict) -> None:
    with _locked_state(state_path):
        cur = read_state(state_path)
        cur.setdefault(source_id, {}).setdefault("tiles", {}).update(tdone)
        write_state_atomic(state_path, cur)


def fetch_tiles(src: Source, tiles: list[tuple[int, int]], root: str | Path, state_path: str | Path, **kw) -> list[dict]:
    """Fetch only the listed GHSL tiles (unrestricted JRC open data). Per-tile sha256 kept in the state sidecar."""
    assert_fetchable(src)
    if not src.tiles:
        raise FetchError(f"{src.source_id} has no tile scheme")
    state_path = Path(state_path)
    state = read_state(state_path)
    rec = state.setdefault(src.source_id, {})
    tdone = rec.setdefault("tiles", {})
    out = []
    for row, col in tiles:
        url = src.tiles["pattern"].format(row=row, col=col)
        name = url.rsplit("/", 1)[-1]
        prev = tdone.get(name, {})
        if prev.get("status") == "absent":
            out.append({"row": row, "col": col, "status": "absent", "skipped": True})
            continue
        try:
            res = download_resumable(url, Path(root) / "incoming" / src.source_id / "tiles" / name,
                                     expected_sha256=prev.get("sha256"), expected_size=prev.get("size_bytes"), **kw)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:   # open-sea tile: legitimately absent
                tdone[name] = {"url": url, "status": "absent"}
                out.append({"row": row, "col": col, "status": "absent", "skipped": False})
                _merge_tiles(state_path, src.source_id, tdone)
                continue
            raise
        tdone[name] = {"url": url, "size_bytes": res["size"], "sha256": res["sha256"]}
        out.append({"row": row, "col": col, **res})
        _merge_tiles(state_path, src.source_id, tdone)
    return out
