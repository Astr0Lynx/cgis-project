"""Deterministic, versioned SQL migrations.

Layout: ``migrations/<track>/<NNNN>_<name>.up.sql`` with a matching ``.down.sql``.
``core`` is required; other tracks (``raster_demo``) are optional and opt-in.

Guarantees:
- each migration runs in its own transaction together with its ledger row;
- a session advisory lock serialises concurrent runners;
- applied migrations are recorded with a SHA-256 of their (LF-normalised) SQL,
  and ``up`` refuses to continue if an applied file has since been edited;
- re-running ``up`` is a no-op once the target is reached.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

CORE = "core"
LOCK_KEY = 0x43474953  # "CGIS"
MIGRATIONS_ENV = "CGIS_DB_MIGRATIONS_DIR"
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Optional tracks and the minimum core version they rely on.
TRACK_REQUIRES = {"raster_demo": (CORE, 2)}

_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.up\.sql$")

LEDGER_DDL = """
CREATE SCHEMA IF NOT EXISTS cgis_meta;
CREATE TABLE IF NOT EXISTS cgis_meta.schema_migrations (
    track            text        NOT NULL,
    version          integer     NOT NULL CHECK (version > 0),
    name             text        NOT NULL,
    checksum_sha256  text        NOT NULL,
    applied_at       timestamptz NOT NULL DEFAULT now(),
    applied_by       text        NOT NULL DEFAULT current_user,
    PRIMARY KEY (track, version)
);
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    track: str
    version: int
    name: str
    up_path: Path
    down_path: Path | None

    @property
    def up_sql(self) -> str:
        return _read_sql(self.up_path)

    @property
    def down_sql(self) -> str:
        if self.down_path is None:
            raise MigrationError(f"{self.label} has no .down.sql; it cannot be rolled back")
        return _read_sql(self.down_path)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.up_sql.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        return f"{self.track}/{self.version:04d}_{self.name}"


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def migrations_dir() -> Path:
    return Path(os.environ.get(MIGRATIONS_ENV) or DEFAULT_MIGRATIONS_DIR)


def available_tracks(root: Path | None = None) -> list[str]:
    root = root or migrations_dir()
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def discover(track: str = CORE, root: Path | None = None) -> list[Migration]:
    root = root or migrations_dir()
    track_dir = root / track
    if not track_dir.is_dir():
        raise MigrationError(f"unknown migration track {track!r} (looked in {track_dir})")
    found: dict[int, Migration] = {}
    for path in sorted(track_dir.glob("*.up.sql")):
        m = _FILE_RE.match(path.name)
        if not m:
            raise MigrationError(f"badly named migration file {path.name}")
        version = int(m.group(1))
        if version in found:
            raise MigrationError(f"duplicate migration version {version} in track {track}")
        down = path.with_name(path.name.replace(".up.sql", ".down.sql"))
        found[version] = Migration(track, version, m.group(2), path, down if down.exists() else None)
    versions = sorted(found)
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"track {track} versions must be contiguous from 1, got {versions}")
    return [found[v] for v in versions]


def _ensure_ledger(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(LEDGER_DDL)


def applied(conn: psycopg.Connection, track: str) -> dict[int, tuple[str, str]]:
    """version -> (name, checksum) for applied migrations of a track."""
    exists = conn.execute("SELECT to_regclass('cgis_meta.schema_migrations') IS NOT NULL").fetchone()[0]
    if not exists:
        return {}
    rows = conn.execute(
        "SELECT version, name, checksum_sha256 FROM cgis_meta.schema_migrations WHERE track = %s",
        (track,),
    ).fetchall()
    return {v: (n, c) for v, n, c in rows}


class _Lock:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        return self

    def __exit__(self, *exc):
        self.conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def _check_drift(migs: list[Migration], done: dict[int, tuple[str, str]]) -> None:
    by_version = {m.version: m for m in migs}
    for version, (name, checksum) in sorted(done.items()):
        m = by_version.get(version)
        if m is None:
            raise MigrationError(f"applied migration {version:04d}_{name} is missing from disk")
        if m.checksum != checksum:
            raise MigrationError(
                f"{m.label} was edited after it was applied (checksum drift); "
                "add a new migration instead of modifying applied ones"
            )


def _require_conn_autocommit(conn: psycopg.Connection) -> None:
    if not conn.autocommit:
        raise MigrationError("migration runner needs an autocommit connection (connect(autocommit=True))")


def up(conn: psycopg.Connection, track: str = CORE, target: int | None = None,
       log=print) -> list[Migration]:
    """Apply pending migrations up to ``target`` (default: latest). Returns those applied."""
    _require_conn_autocommit(conn)
    migs = discover(track)
    with _Lock(conn):
        _ensure_ledger(conn)
        if track in TRACK_REQUIRES:
            dep_track, dep_version = TRACK_REQUIRES[track]
            if max(applied(conn, dep_track), default=0) < dep_version:
                raise MigrationError(
                    f"track {track} requires {dep_track} >= {dep_version}; run `migrate up` first")
        done = applied(conn, track)
        _check_drift(migs, done)
        target = len(migs) if target is None else target
        if not 0 <= target <= len(migs):
            raise MigrationError(f"target {target} out of range 0..{len(migs)} for track {track}")
        if done and max(done) > target:
            raise MigrationError(f"track {track} is at {max(done)} > target {target}; use `migrate down`")
        ran = []
        for m in migs:
            if m.version > target or m.version in done:
                continue
            log(f"applying {m.label}")
            with conn.transaction():
                conn.execute(m.up_sql)
                conn.execute(
                    "INSERT INTO cgis_meta.schema_migrations (track, version, name, checksum_sha256)"
                    " VALUES (%s, %s, %s, %s)",
                    (m.track, m.version, m.name, m.checksum),
                )
            ran.append(m)
        if not ran:
            log(f"track {track}: up to date at version {max(done, default=0)}")
        return ran


def down(conn: psycopg.Connection, track: str = CORE, target: int | None = None,
         steps: int | None = None, log=print) -> list[Migration]:
    """Roll back to ``target`` (inclusive) or by ``steps`` (default 1)."""
    _require_conn_autocommit(conn)
    migs = {m.version: m for m in discover(track)}
    with _Lock(conn):
        done = applied(conn, track)
        if not done:
            log(f"track {track}: nothing to roll back")
            return []
        current = max(done)
        if target is None:
            target = max(current - (1 if steps is None else steps), 0)
        if not 0 <= target <= current:
            raise MigrationError(f"target {target} out of range 0..{current} for track {track}")
        for other, (dep_track, dep_version) in TRACK_REQUIRES.items():
            if dep_track == track and target < dep_version and applied(conn, other):
                raise MigrationError(
                    f"track {other} depends on {track} >= {dep_version}; roll back {other} first")
        _check_drift(list(migs.values()), {v: d for v, d in done.items() if v in migs})
        ran = []
        for version in sorted(done, reverse=True):
            if version <= target:
                break
            m = migs[version]
            log(f"rolling back {m.label}")
            with conn.transaction():
                conn.execute(m.down_sql)
                conn.execute(
                    "DELETE FROM cgis_meta.schema_migrations WHERE track = %s AND version = %s",
                    (track, version),
                )
            ran.append(m)
        return ran


def status(conn: psycopg.Connection) -> list[dict]:
    rows = []
    for track in available_tracks():
        done = applied(conn, track)
        for m in discover(track):
            rec = done.get(m.version)
            state = "pending" if rec is None else ("applied" if rec[1] == m.checksum else "DRIFT")
            rows.append({"track": track, "version": m.version, "name": m.name, "state": state})
        known = {m.version for m in discover(track)}
        for v, (name, _) in sorted(done.items()):
            if v not in known:
                rows.append({"track": track, "version": v, "name": name, "state": "MISSING-FILE"})
    return rows
