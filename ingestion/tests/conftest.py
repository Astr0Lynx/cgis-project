"""Shared fixtures. DB tests need ``INGESTION_TEST_DSN`` pointing at a DISPOSABLE database
(``ingestion/scripts/with_temp_postgis.sh`` provides one); otherwise they are skipped.

Set ``INGESTION_TEST_SCHEMA=existing`` to run against an already-migrated schema (e.g. Agent 2's
migrations during integration) instead of ``tests/sql/contract_schema.sql``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from ingestion import fixtures
from ingestion.manifest import parse_entry

SCHEMA_SQL = Path(__file__).parent / "sql" / "contract_schema.sql"


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    fixtures.generate(out)
    return out


@pytest.fixture
def fresh_fixtures(tmp_path) -> Path:
    """A private, writable copy of the fixtures (tests may tamper with it)."""
    out = tmp_path / "fixtures"
    fixtures.generate(out)
    return out


@pytest.fixture
def assets_dir(tmp_path) -> Path:
    d = tmp_path / "assets"
    d.mkdir()
    return d


def manifest_entries(fixture_dir: Path) -> list[dict]:
    return copy.deepcopy(json.loads((fixture_dir / "manifest.json").read_text())["datasets"])


def entry(fixture_dir: Path, fixture_id: str, **overrides) -> dict:
    e = next(x for x in manifest_entries(fixture_dir) if x["dataset_id"] == fixture_id)
    e.update(overrides)
    return e


def spec(fixture_dir: Path, fixture_id: str, **overrides):
    return parse_entry(entry(fixture_dir, fixture_id, **overrides), fixture_dir)


def raster_entry(path: Path, dataset_id: str = "test-raster", **overrides) -> dict:
    e = {"dataset_id": dataset_id, "version": 1, "title": "Test raster", "kind": "raster",
         "product_level": "source", "year": 2020, "units": "people_per_cell", "source": "unit-test",
         "file": str(path)}
    e.update(overrides)
    return e


def write_raster(path: Path, data: np.ndarray, *, crs="EPSG:4326", transform=None, nodata=-9999.0,
                 dtype=None, count=None, **profile) -> Path:
    data = np.asarray(data)
    if data.ndim == 2:
        data = data[np.newaxis]
    profile_ = {"driver": "GTiff", "width": data.shape[2], "height": data.shape[1], "count": count or data.shape[0],
                "dtype": dtype or str(data.dtype), "crs": crs,
                "transform": transform if transform is not None else from_origin(78.0, 18.0, 0.001, 0.001),
                "nodata": nodata}
    profile_.update(profile)
    if profile_["crs"] is None:
        profile_.pop("crs")
    with rasterio.open(path, "w", **profile_) as dst:
        dst.write(data)
    return path


# ---------------------------------------------------------------------------------------- database
@pytest.fixture(scope="session")
def db_conn():
    dsn = os.environ.get("INGESTION_TEST_DSN")
    if not dsn:
        pytest.skip("INGESTION_TEST_DSN not set (use ingestion/scripts/with_temp_postgis.sh)")
    from ingestion.catalog import connect

    conn = connect(dsn)
    dbname = conn.info.dbname
    if "test" not in dbname:
        conn.close()
        pytest.fail(f"refusing to run destructive DB tests against non-test database {dbname!r}")
    if os.environ.get("INGESTION_TEST_SCHEMA", "contract") != "existing":
        conn.execute("DROP SCHEMA IF EXISTS catalog CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS geo CASCADE")
        conn.execute(SCHEMA_SQL.read_text())
    yield conn
    conn.close()


@pytest.fixture
def db(db_conn):
    db_conn.execute("TRUNCATE catalog.datasets, geo.admin_units")
    yield db_conn
    db_conn.execute("TRUNCATE catalog.datasets, geo.admin_units")


@pytest.fixture
def importer(db, assets_dir):
    from ingestion.catalog import Catalog
    from ingestion.importer import Importer

    return Importer(Catalog(db), assets_dir)
