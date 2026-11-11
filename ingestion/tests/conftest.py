# Shared test fixtures and helpers. DB tests need INGESTION_TEST_DSN pointing at a DISPOSABLE database
# (scripts/with_temp_postgis.sh provides one) and are skipped otherwise. INGESTION_TEST_SCHEMA=existing
# uses the already-migrated schema instead of loading tests/sql/contract_schema.sql.

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


# Session-wide read-only fixture set. Used by most unit tests.
@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    fixtures.generate(out)
    return out


# Private writable fixture set (tests may tamper with it). Used by DB tests.
@pytest.fixture
def fresh_fixtures(tmp_path) -> Path:
    out = tmp_path / "fixtures"
    fixtures.generate(out)
    return out


# Empty assets root. Used by DB tests via `importer`.
@pytest.fixture
def assets_dir(tmp_path) -> Path:
    d = tmp_path / "assets"
    d.mkdir()
    return d


# Copy of a fixture manifest entry with overrides applied. Used by spec() and tests.
def entry(fixture_dir: Path, fixture_id: str, **overrides) -> dict:
    datasets = json.loads((fixture_dir / "manifest.json").read_text())["datasets"]
    e = copy.deepcopy(next(x for x in datasets if x["dataset_id"] == fixture_id))
    e.update(overrides)
    return e


# Parsed DatasetSpec for a fixture dataset with overrides. Used by raster/vector/DB tests.
def spec(fixture_dir: Path, fixture_id: str, **overrides):
    return parse_entry(entry(fixture_dir, fixture_id, **overrides), fixture_dir)


# Parsed DatasetSpec for an ad-hoc raster file. Used by raster and DB tests.
def raster_spec(path: Path, **overrides):
    e = {"dataset_id": "test-raster", "version": 1, "title": "Test raster", "kind": "raster",
         "product_level": "source", "year": 2020, "units": "people_per_cell", "source": "unit-test",
         "file": str(path), **overrides}
    return parse_entry(e, path.parent)


# Parsed DatasetSpec for an ad-hoc GeoJSON file. Used by vector tests.
def vector_spec(path: Path, **overrides):
    e = {"dataset_id": "test-units", "version": 1, "title": "Test units", "kind": "vector",
         "product_level": "source", "source": "unit-test", "file": str(path), **overrides}
    return parse_entry(e, path.parent)


# Write a GeoTIFF from a 2-D or 3-D array (crs=None omits the CRS). Used by raster and DB tests.
def write_raster(path: Path, data: np.ndarray, *, crs="EPSG:4326", transform=None, nodata=-9999.0,
                 **profile) -> Path:
    data = np.asarray(data)
    if data.ndim == 2:
        data = data[np.newaxis]
    profile_ = {"driver": "GTiff", "width": data.shape[2], "height": data.shape[1], "count": data.shape[0],
                "dtype": str(data.dtype),
                "transform": transform if transform is not None else from_origin(78.0, 18.0, 0.001, 0.001),
                "nodata": nodata, **profile}
    if crs is not None:
        profile_["crs"] = crs
    with rasterio.open(path, "w", **profile_) as dst:
        dst.write(data)
    return path


# Session DB connection; refuses non-test databases and loads the stand-in schema unless
# INGESTION_TEST_SCHEMA=existing. Used by `db`.
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


# Connection with empty catalog tables before and after each test. Used by DB tests.
@pytest.fixture
def db(db_conn):
    db_conn.execute("TRUNCATE catalog.datasets, geo.admin_units")
    yield db_conn
    db_conn.execute("TRUNCATE catalog.datasets, geo.admin_units")


# Importer bound to the test DB and a temp assets root. Used by DB tests.
@pytest.fixture
def importer(db, assets_dir):
    from ingestion.catalog import Catalog
    from ingestion.importer import Importer

    return Importer(Catalog(db), assets_dir)
