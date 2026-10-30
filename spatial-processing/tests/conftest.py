"""Shared fixtures: real synthetic GeoTIFFs on disk + a mocked (in-memory) catalog."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

import synthetic  # noqa: E402
from spatial_processing.app import create_app  # noqa: E402
from spatial_processing.catalog import DatasetRecord, InMemoryCatalog  # noqa: E402
from spatial_processing.config import Settings  # noqa: E402
from spatial_processing.jobs import InMemoryJobStore  # noqa: E402

PROVENANCE = {"synthetic": True, "note": "SYNTHETIC deterministic-fixture; not real population"}


def raster_record(dataset_id: str, asset_path: str | None, **overrides) -> DatasetRecord:
    base = DatasetRecord(
        dataset_id=dataset_id,
        version=1,
        kind="raster",
        status="ready",
        crs="EPSG:4326",
        units="people_per_cell",
        nodata=synthetic.NODATA,
        asset_path=asset_path,
        title=f"SYNTHETIC {dataset_id}",
        source="deterministic-fixture",
        provenance=PROVENANCE,
    )
    return replace(base, **overrides)


@pytest.fixture(scope="session")
def asset_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("assets")
    synthetic.contract_raster(root / "synthetic_2001.tif", 1.0)
    synthetic.contract_raster(root / "synthetic_2011.tif", 2.0)
    synthetic.fine_raster(root / "fine_2001.tif", 0.25)
    synthetic.utm_raster(root / "utm_index.tif")
    synthetic.unmasked_int_raster(root / "unmasked_int.tif")
    synthetic.contract_raster(root / "density.tif", 5.0)
    synthetic.multiband_raster(root / "multiband.tif")
    (root / "nested").mkdir()
    synthetic.contract_raster(root / "nested" / "v2.tif", 1.0)
    (root / "corrupted.tif").write_bytes(b"II*\x00 this is not really a tiff")
    (root / "fake.vrt").write_text("<VRTDataset/>")
    return root


@pytest.fixture(scope="session")
def outside_file(tmp_path_factory) -> Path:
    """A private file OUTSIDE the asset root that must never be readable through the API."""
    private = tmp_path_factory.mktemp("private")
    return synthetic.contract_raster(private / "secret.tif", 42.0)


@pytest.fixture()
def catalog(asset_root, outside_file) -> InMemoryCatalog:
    link = asset_root / "escape.tif"
    if not link.exists():
        link.symlink_to(outside_file)
    nodata = synthetic.NODATA
    return InMemoryCatalog(
        [
            raster_record("synthetic-worldpop-2001", "synthetic_2001.tif", year=2001),
            raster_record("synthetic-worldpop-2011", "synthetic_2011.tif", year=2011),
            raster_record("fine-2001", "fine_2001.tif"),
            raster_record("utm-index", "utm_index.tif", crs="EPSG:32644", units="count", nodata=-1),
            raster_record("unmasked-int", "unmasked_int.tif", units="count", nodata=None),
            raster_record("density", "density.tif", units="people_per_km2"),
            raster_record("unknown-units", "synthetic_2001.tif", units="index_value"),
            raster_record("multiband", "multiband.tif"),
            # versioning: v1 ready, v2 ready -> v2 (nested/v2.tif) is current; v3 staged is ignored
            raster_record("versioned", "synthetic_2011.tif", version=1),
            raster_record("versioned", "nested/v2.tif", version=2),
            raster_record("versioned", "missing.tif", version=3, status="staged"),
            raster_record("staged-only", "synthetic_2001.tif", status="staged"),
            raster_record("failed-only", "synthetic_2001.tif", status="failed"),
            DatasetRecord("synthetic-towns", 1, "vector", "ready", "EPSG:4326", None, None, None),
            # hostile / broken catalog rows
            raster_record("evil-traversal", "../private/secret.tif"),
            raster_record("evil-absolute", str(outside_file)),
            raster_record("evil-url", "https://example.com/data.tif"),
            raster_record("evil-vsicurl", "/vsicurl/https://example.com/data.tif"),
            raster_record("evil-symlink", "escape.tif"),
            raster_record("evil-vrt", "fake.vrt"),
            raster_record("evil-null", None),
            raster_record("missing-file", "does_not_exist.tif"),
            raster_record("corrupted", "corrupted.tif"),
            raster_record("absolute-inside", str(asset_root / "synthetic_2001.tif"), nodata=nodata),
        ]
    )


@pytest.fixture()
def make_client(asset_root, catalog, tmp_path):
    """Factory: build an app with optional Settings overrides; returns (client, job_store, settings)."""

    opened: list[TestClient] = []

    def factory(**overrides):
        settings = Settings(
            asset_root=asset_root,
            output_root=tmp_path / "outputs",
            catalog_backend="memory",
            job_store_backend="memory",
            root_path="/api",
            **overrides,
        )
        store = InMemoryJobStore()
        app = create_app(settings, catalog=catalog, job_store=store)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()  # run lifespan
        opened.append(client)
        return client, store, settings

    yield factory
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture()
def client(make_client):
    return make_client()[0]


@pytest.fixture()
def client_and_store(make_client):
    return make_client()
