# PostgreSQL access (psycopg 3): a bounded pool for the API, plain connections for workers and CLI.
# All statements are parameterised.

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import redact_dsn

log = logging.getLogger(__name__)


# Lazy connection pool yielding autocommit dict-row connections. Created by app.create_app.
class Database:
    # Remember the DSN and pool bounds; the pool opens on first use or in the app lifespan.
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8, application_name: str = "cgis-app"):
        self.dsn = dsn
        self.application_name = application_name
        self._pool = None
        self._min = min_size
        self._max = max_size

    # Open the psycopg_pool pool (idempotent). Called by the app lifespan and connection().
    def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        from psycopg_pool import ConnectionPool

        if self._pool is not None:
            return
        self._pool = ConnectionPool(
            self.dsn,
            min_size=self._min,
            max_size=self._max,
            kwargs={"autocommit": True, "row_factory": dict_row, "application_name": self.application_name,
                    "connect_timeout": 5},
            open=False,
            check=ConnectionPool.check_connection,
            name=self.application_name,
            timeout=10.0,
        )
        self._pool.open(wait=wait, timeout=timeout)

    # Close the pool. Called when the app shuts down.
    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    # Context manager borrowing one pooled connection. Used by every route.
    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        if self._pool is None:
            self.open()
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            yield conn

    # Check the cgis_app and catalog schemas are reachable. Used by GET /api/app/ready.
    def ping(self) -> None:
        with self.connection() as conn:
            conn.execute("SELECT 1 FROM cgis_app.users LIMIT 0")
            conn.execute("SELECT 1 FROM catalog.datasets LIMIT 0")


# One autocommit dict-row connection, retried until wait_seconds elapse (DSN redacted in errors).
# Used by the CLI, the workers and the heartbeat thread.
def connect(dsn: str, *, application_name: str, wait_seconds: float = 0.0) -> psycopg.Connection:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, application_name=application_name,
                                   connect_timeout=5)
        except psycopg.OperationalError as exc:
            if time.monotonic() >= deadline:
                first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                raise RuntimeError(f"cannot connect to database ({redact_dsn(dsn)}): {first}") from exc
            time.sleep(1.0)

