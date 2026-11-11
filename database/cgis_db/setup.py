# One-shot database setup, the CMD of the `migrate` Compose service (database/Dockerfile):
# wait for PostgreSQL, apply the migrations, then create the per-service login roles.
# Safe to re-run; it also re-applies passwords, so a password changed in .env takes effect on `up`.
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import psycopg
from psycopg import sql

# (user env var, password env var, group role created by migration 0006, read-only sessions)
SERVICE_ROLES = (
    ("INGEST_DB_USER", "INGEST_DB_PASSWORD", "cgis_ingest", False),
    ("OGC_DB_USER", "OGC_DB_PASSWORD", "cgis_reader", True),
    ("PROCESSING_DB_USER", "PROCESSING_DB_PASSWORD", "cgis_processing", False),
)
GROUP_ROLES = {group for _, _, group, _ in SERVICE_ROLES}
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PLACEHOLDER = re.compile(r"change[-_]?me", re.IGNORECASE)
# A role named like a project schema would get that schema first in the default search_path
# ("$user", public), so unqualified CREATEs would land in it.
PROJECT_SCHEMAS = {"cgis", "catalog", "geo", "ops", "cgis_meta", "raster_demo", "cgis_app"}
WAIT_SECONDS = float(os.environ.get("CGIS_DB_WAIT_SECONDS", "60"))


# Print an error and exit 1. Used by every step below.
def fail(message: str) -> None:
    print(f"setup_db: ERROR: {message}", file=sys.stderr)
    sys.exit(1)


# Block until PostgreSQL (PG* env) accepts connections. Covers restarts that bypass the
# Compose healthcheck ordering. Used by main.
def wait_for_db() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    last_error = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect("", connect_timeout=3, application_name="cgis_setup_db") as conn:
                conn.execute("SELECT 1")
            print("setup_db: database is accepting connections")
            return
        except psycopg.Error as exc:
            last_error = exc.__class__.__name__
            time.sleep(1)
    fail(f"database not reachable after {WAIT_SECONDS:.0f}s ({last_error})")


# Validate the service user names/passwords from the environment (.env). Used by main.
def read_roles() -> list[tuple[str, str, str, bool]]:
    owner = os.environ.get("PGUSER", "")
    if owner in PROJECT_SCHEMAS:
        fail(f"POSTGRES_USER={owner} equals a project schema name; choose another owner name "
             "(for example cgis_admin) and recreate the db volume")
    roles, seen = [], set()
    for user_var, password_var, group, read_only in SERVICE_ROLES:
        user = os.environ.get(user_var, "")
        password = os.environ.get(password_var, "")
        if not ROLE_NAME.match(user):
            fail(f"{user_var} must match {ROLE_NAME.pattern}")
        if user in GROUP_ROLES or user in PROJECT_SCHEMAS or user == owner or user in seen:
            fail(f"{user_var}={user} must differ from the owner, the group roles and the other service users")
        if len(password) < 12 or PLACEHOLDER.search(password):
            fail(f"{password_var} is missing, shorter than 12 characters or still a placeholder; "
                 "run infra/scripts/init-env.sh or edit .env")
        seen.add(user)
        roles.append((user, password, group, read_only))
    return roles


# Create or update one LOGIN role per service as a member of its group role. Used by main.
def provision_roles(roles: list[tuple[str, str, str, bool]]) -> None:
    with psycopg.connect("", application_name="cgis_setup_db", autocommit=True) as conn:
        exists = lambda name: conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
        missing = [g for g in sorted(GROUP_ROLES) if not exists(g)]
        if missing:
            fail(f"group roles {missing} do not exist; the migrations did not run")
        dbname = conn.info.dbname
        for user, password, group, read_only in roles:
            found = exists(user)
            verb = "ALTER" if found else "CREATE"
            conn.execute(sql.SQL(verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}")
                         .format(sql.Identifier(user), sql.Literal(password)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(user)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                         .format(sql.Identifier(dbname), sql.Identifier(user)))
            conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = {}")
                         .format(sql.Identifier(user), sql.Literal("on" if read_only else "off")))
            print(f"setup_db: {'updated' if found else 'created'} login role {user} (member of {group}"
                  f"{', read-only sessions' if read_only else ''})")


# Entry point (CMD of the migrate image): validate config, wait, migrate, provision roles.
def main() -> None:
    roles = read_roles()
    wait_for_db()
    if subprocess.run([sys.executable, "-m", "cgis_db", "migrate", "up"]).returncode != 0:
        fail("migrations failed")
    provision_roles(roles)
    print("setup_db: ok")


if __name__ == "__main__":
    main()
