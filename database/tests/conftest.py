"""Integration-test fixtures: every test runs against a REAL PostgreSQL/PostGIS server.

Set ``CGIS_TEST_ADMIN_DSN`` to a role that may CREATE/DROP DATABASE, e.g.
``postgresql://postgres@localhost:55432/postgres``. Each test session creates
throw-away databases named ``cgis_test_*`` and drops them afterwards.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cgis_db import fixtures, migrate  # noqa: E402

ADMIN_ENV = "CGIS_TEST_ADMIN_DSN"


def _admin_dsn() -> str:
    dsn = os.environ.get(ADMIN_ENV)
    if not dsn:
        pytest.fail(f"{ADMIN_ENV} is not set; these are PostGIS integration tests and need a real server",
                    pytrace=False)
    return dsn


class DatabaseFactory:
    def __init__(self, admin_dsn: str):
        self.admin_dsn = admin_dsn
        self.created: list[str] = []

    def create(self) -> str:
        name = f"cgis_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        self.created.append(name)
        return make_conninfo(self.admin_dsn, dbname=name)

    def drop_all(self) -> None:
        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            for name in self.created:
                c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def db_factory():
    factory = DatabaseFactory(_admin_dsn())
    yield factory
    factory.drop_all()


def _quiet(_msg):
    pass


@pytest.fixture
def fresh_dsn(db_factory) -> str:
    """An empty database (no migrations)."""
    return db_factory.create()


@pytest.fixture
def migrated_dsn(db_factory) -> str:
    """A database with core migrations applied and nothing else; committed state allowed."""
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        migrate.up(c, log=_quiet)
    return dsn


@pytest.fixture(scope="session")
def shared_dsn(db_factory) -> str:
    """Session-wide migrated database with the synthetic towns loaded. Tests must not commit to it."""
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        migrate.up(c, log=_quiet)
    with psycopg.connect(dsn) as c:
        fixtures.load_units(c)
    return dsn


@pytest.fixture
def conn(shared_dsn):
    """Connection to the shared database inside a transaction that is always rolled back."""
    with psycopg.connect(shared_dsn) as c:
        yield c
        c.rollback()


def raster_record(**overrides) -> dict:
    """A complete, valid raster catalog record following the SYNTHETIC fixture contract."""
    rec = {
        "dataset_id": "synthetic-worldpop-2001",
        "version": 1,
        "title": "SYNTHETIC population raster (2001)",
        "kind": "raster",
        "product_level": "source",
        "year": 2001,
        "crs": "EPSG:4326",
        "bbox": [78.000, 17.936, 78.064, 18.000],
        "resolution_x": 0.001,
        "resolution_y": 0.001,
        "units": "people_per_cell",
        "nodata": -9999,
        "asset_path": "synthetic/synthetic_2001.tif",
        "checksum_sha256": "a" * 64,
        "source": "deterministic-fixture",
        "provenance": {"synthetic": True},
        "status": "ready",
    }
    rec.update(overrides)
    return rec
