# Pytest fixtures. Every test runs against a real PostgreSQL/PostGIS server: CGIS_TEST_ADMIN_DSN must
# name a role that may CREATE/DROP DATABASE; throw-away cgis_test_* databases are dropped at the end.

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

# Import cgis_db from this source tree, even when an installed copy exists.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cgis_db import fixtures, migrate  # noqa: E402

ADMIN_ENV = "CGIS_TEST_ADMIN_DSN"


# Admin DSN from CGIS_TEST_ADMIN_DSN, or a clear failure. Used by db_factory.
def _admin_dsn() -> str:
    dsn = os.environ.get(ADMIN_ENV)
    if not dsn:
        pytest.fail(f"{ADMIN_ENV} is not set; these are PostGIS integration tests and need a real server",
                    pytrace=False)
    return dsn


# Creates uniquely named throw-away databases and drops them all at session end.
class DatabaseFactory:
    def __init__(self, admin_dsn: str):
        self.admin_dsn = admin_dsn
        self.created: list[str] = []

    # New empty database; returns its DSN. Used by the fixtures below and tests/test_raster_demo.py.
    def create(self) -> str:
        name = f"cgis_test_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        self.created.append(name)
        return make_conninfo(self.admin_dsn, dbname=name)

    # Drops every database this factory created. Used by db_factory teardown.
    def drop_all(self) -> None:
        with psycopg.connect(self.admin_dsn, autocommit=True) as c:
            for name in self.created:
                c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


# Session-wide DatabaseFactory.
@pytest.fixture(scope="session")
def db_factory():
    factory = DatabaseFactory(_admin_dsn())
    yield factory
    factory.drop_all()


# No-op log= callback for migrate.up/down. Also used by test_migrations.py, test_raster_demo.py
# and test_versioned_units.py.
def quiet(_msg):
    pass


# An empty database (no migrations).
@pytest.fixture
def fresh_dsn(db_factory) -> str:
    return db_factory.create()


# A database with core migrations only; tests may commit to it.
@pytest.fixture
def migrated_dsn(db_factory) -> str:
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        migrate.up(c, log=quiet)
    return dsn


# Session-wide migrated database with the stub towns loaded; tests must not commit to it.
@pytest.fixture(scope="session")
def shared_dsn(db_factory) -> str:
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        migrate.up(c, log=quiet)
    with psycopg.connect(dsn) as c:
        fixtures.load_units(c)
    return dsn


# Connection to the shared database whose transaction is always rolled back.
@pytest.fixture
def conn(shared_dsn):
    with psycopg.connect(shared_dsn) as c:
        yield c
        c.rollback()


# A complete, valid ready raster catalog record (synthetic), with overrides. Used by test_catalog.py.
def raster_record(**overrides) -> dict:
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
