"""Create or update the per-service PostgreSQL login roles (run by the `migrate` service).

Agent 2's migration 0006 creates three NOLOGIN group roles that describe each service's
privileges: cgis_ingest, cgis_reader and cgis_processing. This script creates one LOGIN role
per service, with its password taken from the environment (.env), and makes it a member of
the matching group. Re-running it is safe; it also re-applies the password, so changing a
password in .env takes effect on the next `docker compose up`.

Connection: the owner/admin role from the standard PG* environment variables.
"""

from __future__ import annotations

import os
import re
import sys

import psycopg
from psycopg import sql

# (user variable, password variable, group role from database/migrations/core/0006, read-only)
SERVICE_ROLES = (
    ("INGEST_DB_USER", "INGEST_DB_PASSWORD", "cgis_ingest", False),
    ("OGC_DB_USER", "OGC_DB_PASSWORD", "cgis_reader", True),
    ("PROCESSING_DB_USER", "PROCESSING_DB_PASSWORD", "cgis_processing", False),
)
GROUP_ROLES = {group for _, _, group, _ in SERVICE_ROLES}
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PLACEHOLDER = re.compile(r"change[-_]?me", re.IGNORECASE)
# Schemas created by database/migrations. A role with one of these names would get that schema
# first in the default search_path ("$user", public), so unqualified CREATEs would land in it.
PROJECT_SCHEMAS = {"cgis", "catalog", "geo", "ops", "cgis_meta", "raster_demo", "cgis_app"}


def fail(message: str) -> None:
    print(f"provision_roles: ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def read_config() -> list[tuple[str, str, str, bool]]:
    roles = []
    seen = set()
    owner = os.environ.get("PGUSER", "")
    if owner in PROJECT_SCHEMAS:
        fail(f"POSTGRES_USER={owner} equals a project schema name; choose another owner name "
             "(for example cgis_admin) and recreate the db volume")
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


def app_grants() -> None:
    """``--app-grants``: read access for the OGC publisher to the application API's per-dataset access
    levels (Agent 5 CR R5), so admins-only datasets are never published by OGC/Tiles. Column-level and
    read-only. Run after the application API's own migration (Compose service ``app-grants``)."""
    with psycopg.connect("", application_name="cgis_provision_roles", autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('cgis_app.dataset_access') IS NULL").fetchone()[0]:
            print("provision_roles: cgis_app.dataset_access not present; nothing to grant")
            return
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'cgis_reader'").fetchone() is None:
            fail("group role cgis_reader does not exist; run the database migrations first")
        conn.execute("GRANT USAGE ON SCHEMA cgis_app TO cgis_reader")
        conn.execute("GRANT SELECT (dataset_id, access) ON cgis_app.dataset_access TO cgis_reader")
    print("provision_roles: cgis_reader may read cgis_app.dataset_access(dataset_id, access)")


def main() -> None:
    if "--app-grants" in sys.argv[1:]:
        app_grants()
        return
    roles = read_config()
    with psycopg.connect("", application_name="cgis_provision_roles", autocommit=True) as conn:
        missing = [g for g in sorted(GROUP_ROLES)
                   if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (g,)).fetchone() is None]
        if missing:
            fail(f"group roles {missing} do not exist; migrations must run first (python -m cgis_db migrate up)")
        dbname = conn.info.dbname
        for user, password, group, read_only in roles:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}")
                         .format(sql.Identifier(user), sql.Literal(password)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(user)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                         .format(sql.Identifier(dbname), sql.Identifier(user)))
            conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = {}")
                         .format(sql.Identifier(user), sql.Literal("on" if read_only else "off")))
            print(f"provision_roles: {'updated' if exists else 'created'} login role {user} (member of {group}"
                  f"{', read-only sessions' if read_only else ''})")
    print("provision_roles: ok")


if __name__ == "__main__":
    main()
