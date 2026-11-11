# `cgis-app migrate`: apply migrations/NNNN_*.sql once each (checksummed; a changed applied file is refused as
# drift) and create/update the APP_DB_USER / APP_WORKER_DB_USER login roles. Run as the DB owner after the
# database/ migrations (Compose one-shot app-migrate).

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from importlib import resources

import psycopg
from psycopg import sql

log = logging.getLogger(__name__)

LOCK_KEY = "cgis_app.migrate"
MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
ROLE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PLACEHOLDER_RE = re.compile(r"change[-_]?me", re.IGNORECASE)
# Names that must not be used for login roles: group roles and project schemas (a role named like a
# schema puts that schema first in the default search_path).
RESERVED_ROLE_NAMES = {
    "cgis_reader", "cgis_ingest", "cgis_processing", "cgis_app_api", "cgis_app_worker",
    "cgis", "catalog", "geo", "ops", "cgis_meta", "raster_demo", "cgis_app", "public", "postgres",
}
LOGIN_ROLES = (
    ("APP_DB_USER", "APP_DB_PASSWORD", "cgis_app_api"),
    ("APP_WORKER_DB_USER", "APP_WORKER_DB_PASSWORD", "cgis_app_worker"),
)


# Migration or role configuration problem. Printed by cli migrate.
class MigrationError(RuntimeError):
    pass


# One SQL migration file.
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    # SHA-256 of the SQL text, recorded in cgis_app.schema_migrations.
    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


# Packaged migrations in order (versions must be contiguous from 0001). Used by apply/status.
def load_migrations() -> list[Migration]:
    out = []
    for entry in resources.files("cgis_app.migrations").iterdir():
        m = MIGRATION_RE.match(entry.name)
        if m:
            out.append(Migration(int(m.group(1)), m.group(2), entry.read_text(encoding="utf-8")))
    out.sort(key=lambda m: m.version)
    versions = [m.version for m in out]
    if versions != list(range(1, len(out) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 0001, got {versions}")
    return out


# Apply pending migrations under an advisory lock, refusing drift; returns the names applied.
# Used by run and tests/conftest.py.
def apply_migrations(conn: psycopg.Connection) -> list[str]:
    applied: list[str] = []
    conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (LOCK_KEY,))
    try:
        if conn.execute("SELECT to_regclass('catalog.datasets') IS NOT NULL AS ok").fetchone()[0] is not True:
            raise MigrationError("catalog.datasets does not exist: run the database migrations first "
                                 "(python -m cgis_db migrate up)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS cgis_app")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cgis_app.schema_migrations ("
            " version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0]: r[2] for r in conn.execute(
            "SELECT version, name, checksum FROM cgis_app.schema_migrations").fetchall()}
        for mig in load_migrations():
            if mig.version in done:
                if done[mig.version] != mig.checksum:
                    raise MigrationError(f"migration {mig.version:04d}_{mig.name} changed after it was applied "
                                         "(drift); add a new migration instead of editing an applied one")
                continue
            with conn.transaction():
                conn.execute(mig.sql)
                conn.execute("INSERT INTO cgis_app.schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                             (mig.version, mig.name, mig.checksum))
            applied.append(f"{mig.version:04d}_{mig.name}")
            log.info("applied migration %04d_%s", mig.version, mig.name)
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK_KEY,))
    return applied


# applied/pending/DRIFT per migration. Used by `cgis-app migrate --status`.
def migration_status(conn: psycopg.Connection) -> list[dict]:
    exists = conn.execute("SELECT to_regclass('cgis_app.schema_migrations') IS NOT NULL").fetchone()[0]
    done = {}
    if exists:
        done = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM cgis_app.schema_migrations")}
    rows = []
    for mig in load_migrations():
        state = "pending" if mig.version not in done else ("applied" if done[mig.version] == mig.checksum else "DRIFT")
        rows.append({"version": mig.version, "name": mig.name, "state": state})
    return rows


# Validated (user, password, group) triples from APP_*_DB_USER/PASSWORD. Used by run.
def read_login_roles(env: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    env = dict(os.environ if env is None else env)
    owner = env.get("PGUSER", "")
    roles = []
    seen: set[str] = set()
    for user_var, pass_var, group in LOGIN_ROLES:
        user = env.get(user_var, "")
        password = env.get(pass_var, "")
        if not user and not password:
            continue  # optional: a deployment may provision only one of the services
        if not ROLE_NAME_RE.match(user):
            raise MigrationError(f"{user_var} must match {ROLE_NAME_RE.pattern}")
        if user in RESERVED_ROLE_NAMES or user == owner or user in seen:
            raise MigrationError(f"{user_var}={user} must differ from the owner, group roles, schemas and "
                                 "the other service users")
        if len(password) < 16 or PLACEHOLDER_RE.search(password):
            raise MigrationError(f"{pass_var} is missing, shorter than 16 characters or still a placeholder")
        seen.add(user)
        roles.append((user, password, group))
    return roles


# Create or update the login roles and grant their group + CONNECT. Used by run and tests.
def provision_login_roles(conn: psycopg.Connection, roles: list[tuple[str, str, str]]) -> list[str]:
    done = []
    dbname = conn.info.dbname
    for user, password, group in roles:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        # INHERIT: the importer and the API rely on the group privileges without SET ROLE.
        conn.execute(sql.SQL(verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                             "NOBYPASSRLS INHERIT PASSWORD {}")
                     .format(sql.Identifier(user), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(user)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname),
                                                                        sql.Identifier(user)))
        done.append(f"{'updated' if exists else 'created'} {user} (member of {group})")
        log.info("login role %s %s (member of %s)", user, "updated" if exists else "created", group)
    return done


# Migrations plus optional role provisioning on one connection. Used by cli migrate.
def run(dsn: str = "", *, provision: bool = True) -> dict:
    roles = read_login_roles() if provision else []
    with psycopg.connect(dsn, autocommit=True, application_name="cgis_app_migrate") as conn:
        applied = apply_migrations(conn)
        provisioned = provision_login_roles(conn, roles) if provision else []
    return {"applied": applied, "roles": provisioned}
