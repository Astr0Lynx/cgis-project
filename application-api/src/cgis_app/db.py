"""Database access helpers (psycopg 3). Importable without FastAPI.

The API uses a bounded connection pool; workers use one plain connection each. Every statement is
parameterised; no client text is ever interpolated into SQL.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .config import redact_dsn

log = logging.getLogger(__name__)


class Database:
    """Thin wrapper: ``with db.connection() as conn`` yields an autocommit dict-row connection."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8, application_name: str = "cgis-app"):
        self.dsn = dsn
        self.application_name = application_name
        self._pool = None
        self._min = min_size
        self._max = max_size

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

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        if self._pool is None:
            self.open()
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            yield conn

    def ping(self) -> None:
        with self.connection() as conn:
            conn.execute("SELECT 1 FROM cgis_app.users LIMIT 0")
            conn.execute("SELECT 1 FROM catalog.datasets LIMIT 0")


def connect(dsn: str, *, application_name: str, wait_seconds: float = 0.0) -> psycopg.Connection:
    """One autocommit dict-row connection, retrying until ``wait_seconds`` elapse."""
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


def one(conn: psycopg.Connection, query: str, params: Any = None) -> dict | None:
    return conn.execute(query, params).fetchone()


def all_rows(conn: psycopg.Connection, query: str, params: Any = None) -> list[dict]:
    return conn.execute(query, params).fetchall()
