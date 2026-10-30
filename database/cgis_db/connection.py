"""Database connection resolution.

Order of precedence:
1. explicit ``dsn`` argument (CLI ``--dsn``)
2. ``CGIS_DATABASE_URL``
3. ``DATABASE_URL``
4. libpq environment variables (``PGHOST``, ``PGPORT``, ``PGDATABASE``, ``PGUSER``,
   ``PGPASSWORD``/``PGPASSFILE``, ``PGSSLMODE`` ...), which libpq reads itself.

Passwords are never logged; :func:`redact` strips them from DSNs before display.
"""

from __future__ import annotations

import os
import re

import psycopg

ENV_VARS = ("CGIS_DATABASE_URL", "DATABASE_URL")


def resolve_dsn(dsn: str | None = None) -> str:
    if dsn:
        return dsn
    for name in ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return ""  # empty conninfo: libpq falls back to PG* environment variables


def connect(dsn: str | None = None, **kwargs) -> psycopg.Connection:
    kwargs.setdefault("application_name", "cgis_db")
    return psycopg.connect(resolve_dsn(dsn), **kwargs)


def redact(dsn: str) -> str:
    dsn = re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", dsn)
    return re.sub(r"(password\s*=\s*)\S+", r"\1***", dsn, flags=re.IGNORECASE)
