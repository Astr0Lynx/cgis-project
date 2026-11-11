# Versioned SQL migration runner: migrations/<track>/<NNNN>_<name>.up.sql (+ .down.sql).
# Each migration runs in its own transaction with its ledger row; an advisory lock serialises
# concurrent runners and a SHA-256 per applied file detects later edits ("drift").

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
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


# Reads a SQL file with line endings normalised to LF (so CRLF checkouts do not cause drift).
# Used by Migration.up_sql / down_sql.
def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@dataclass(frozen=True)
class Migration:
    track: str
    version: int
    name: str
    up_path: Path
    down_path: Path | None

    # SQL of the .up.sql file. Used by up(), checksum and tests/test_migrations.py.
    @property
    def up_sql(self) -> str:
        return _read_sql(self.up_path)

    # SQL of the .down.sql file. Used by down().
    @property
    def down_sql(self) -> str:
        if self.down_path is None:
            raise MigrationError(f"{self.label} has no .down.sql; it cannot be rolled back")
        return _read_sql(self.down_path)

    # SHA-256 of the normalised up SQL, stored in the ledger. Used by up(), status(), _check_drift().
    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.up_sql.encode("utf-8")).hexdigest()

    # "track/NNNN_name" for log and error messages.
    @property
    def label(self) -> str:
        return f"{self.track}/{self.version:04d}_{self.name}"


# Migrations root: $CGIS_DB_MIGRATIONS_DIR (set in database/Dockerfile) or database/migrations.
# Used by discover(), status() and tests/test_migrations.py.
def migrations_dir() -> Path:
    return Path(os.environ.get(MIGRATIONS_ENV) or DEFAULT_MIGRATIONS_DIR)


# Lists the migrations of a track in version order, checking names and contiguous numbering.
# Used by up(), down(), status() and the tests.
def discover(track: str = CORE) -> list[Migration]:
    track_dir = migrations_dir() / track
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


# Applied migrations of a track as {version: (name, checksum)}; empty if there is no ledger yet.
# Used by up(), down(), status() and the tests.
def applied(conn: psycopg.Connection, track: str) -> dict[int, tuple[str, str]]:
    if not conn.execute("SELECT to_regclass('cgis_meta.schema_migrations') IS NOT NULL").fetchone()[0]:
        return {}
    rows = conn.execute(
        "SELECT version, name, checksum_sha256 FROM cgis_meta.schema_migrations WHERE track = %s",
        (track,),
    ).fetchall()
    return {v: (n, c) for v, n, c in rows}


# Holds the session advisory lock that serialises concurrent runners. Used by up() and down().
@contextmanager
def _locked(conn: psycopg.Connection):
    if not conn.autocommit:
        raise MigrationError("migration runner needs an autocommit connection (connect(autocommit=True))")
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    try:
        yield
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


# Raises if an applied migration is missing on disk or was edited after it was applied.
# Used by up() and down().
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


# Applies pending migrations up to target (default: latest) and returns those applied.
# Used by cli.cmd_migrate / cmd_raster_demo, setup.main (python -m cgis_db migrate up) and the tests.
def up(conn: psycopg.Connection, track: str = CORE, target: int | None = None,
       log=print) -> list[Migration]:
    migs = discover(track)
    with _locked(conn):
        with conn.transaction():
            conn.execute(LEDGER_DDL)
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


# Rolls back every migration above target, or the last `steps` (default 1); returns those rolled back.
# Used by cli.cmd_migrate and the tests.
def down(conn: psycopg.Connection, track: str = CORE, target: int | None = None,
         steps: int | None = None, log=print) -> list[Migration]:
    migs = {m.version: m for m in discover(track)}
    with _locked(conn):
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


# One row per migration of every track: pending / applied / DRIFT / MISSING-FILE.
# Used by cli.cmd_migrate (migrate status) and tests/test_migrations.py.
def status(conn: psycopg.Connection) -> list[dict]:
    rows = []
    for track in sorted(p.name for p in migrations_dir().iterdir() if p.is_dir()):
        done = applied(conn, track)
        migs = discover(track)
        for m in migs:
            rec = done.get(m.version)
            state = "pending" if rec is None else ("applied" if rec[1] == m.checksum else "DRIFT")
            rows.append({"track": track, "version": m.version, "name": m.name, "state": state})
        known = {m.version for m in migs}
        for v, (name, _) in sorted(done.items()):
            if v not in known:
                rows.append({"track": track, "version": v, "name": name, "state": "MISSING-FILE"})
    return rows
