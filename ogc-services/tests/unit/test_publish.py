"""Catalog -> pygeoapi configuration generation (no database needed)."""

from pathlib import Path

import pytest
import yaml

from cgis_ogc.catalog import CatalogDataset
from cgis_ogc.publish import (
    FEATURE_PROVIDER,
    TILE_PROVIDER,
    PublishSettings,
    build_config,
    dump_config,
    vector_static_filter,
)

BASE = yaml.safe_load((Path(__file__).parents[2] / "pygeoapi" / "config" / "pygeoapi.base.yml").read_text())
COLUMNS = ["unit_id", "name", "level", "geom"]
BBOX = (10.0, 20.0, 11.0, 21.0)


def vector(dataset_id="any-vector", **kw):
    return CatalogDataset(dataset_id=dataset_id, version=1, title=f"{dataset_id} title", kind="vector", bbox=BBOX, **kw)


def raster(dataset_id="any-raster", **kw):
    kw.setdefault("asset_path", f"{dataset_id}/v1/data.tif")
    kw.setdefault("crs", "EPSG:4326")
    kw.setdefault("resolution_x", 0.001)
    kw.setdefault("resolution_y", 0.001)
    return CatalogDataset(dataset_id=dataset_id, version=2, title=f"{dataset_id} title", kind="raster", bbox=BBOX, **kw)


@pytest.fixture
def settings():
    return PublishSettings(env={"TILES_PUBLIC_URL": "http://gw/tiles/"})


def test_resources_follow_catalog_not_hardcoded_ids(settings):
    datasets = [vector("zz-boundaries"), raster("aa-elevation"), raster("mm-landcover", year=2015)]
    resources = build_config(BASE, datasets, COLUMNS, settings)["resources"]
    assert list(resources) == ["aa-elevation", "mm-landcover", "zz-boundaries"]
    assert resources["zz-boundaries"]["providers"][0]["name"] == FEATURE_PROVIDER
    assert resources["aa-elevation"]["providers"][0]["name"] == TILE_PROVIDER


def test_adding_a_catalog_row_adds_a_collection(settings):
    before = build_config(BASE, [vector("a")], COLUMNS, settings)["resources"]
    after = build_config(BASE, [vector("a"), raster("b")], COLUMNS, settings)["resources"]
    assert set(after) - set(before) == {"b"}


def test_vector_resource(settings):
    res = build_config(BASE, [vector("v", source="deterministic-fixture")], COLUMNS, settings)["resources"]["v"]
    provider = res["providers"][0]
    assert res["type"] == "collection"
    assert res["extents"]["spatial"] == {"bbox": list(BBOX), "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
    assert provider["table"] == "admin_units" and provider["id_field"] == "unit_id"
    assert provider["storage_crs"].endswith("/OGC/1.3/CRS84")
    assert "http://www.opengis.net/def/crs/EPSG/0/4326" in provider["crs"]
    assert "editable" not in provider
    assert provider["data"]["password"] == "${POSTGRES_PASSWORD}"
    assert "deterministic-fixture" in res["keywords"]


def test_raster_resource(settings):
    res = build_config(BASE, [raster("r", year=2011, units="people_per_cell", nodata=-9999)], COLUMNS, settings)["resources"]["r"]
    provider = res["providers"][0]
    assert provider["type"] == "tile"
    assert provider["data"] == "r"  # a catalog id, never a path or URL
    assert provider["format"] == {"name": "png", "mimetype": "image/png"}
    assert provider["options"]["schemes"] == ["WebMercatorQuad", "WorldCRS84Quad"]
    assert provider["options"]["zoom"] == {"min": 0, "max": 13}
    assert res["extents"]["temporal"]["begin"].startswith("2011-01-01")
    assert "NoData -9999" in res["description"] and "people_per_cell" in res["description"]
    assert res["links"][0]["href"] == "http://gw/tiles/datasets/r/WebMercatorQuad/tilejson.json"
    assert "vendor-specific" in res["links"][0]["title"]


@pytest.mark.parametrize("dataset", [
    vector("staged", status="staged"),
    vector("bad/id"),
    vector("../etc"),
    CatalogDataset(dataset_id="no-bbox", version=1, title="t", kind="vector", bbox=None),
    raster("no-asset", asset_path=None),
    CatalogDataset(dataset_id="weird", version=1, title="t", kind="mesh", bbox=BBOX),
])
def test_unpublishable_rows_are_skipped(settings, dataset):
    assert build_config(BASE, [dataset], COLUMNS, settings)["resources"] == {}


def test_vector_skipped_when_table_missing(settings):
    assert build_config(BASE, [vector("v")], [], settings)["resources"] == {}


def test_static_filter_prefers_dataset_id_column():
    ds = vector("v", provenance={"ogc_publication": {"filter": {"level": "x"}}})
    assert vector_static_filter(ds, COLUMNS + ["dataset_id"]) == {"dataset_id": "v"}


def test_static_filter_from_provenance_hint():
    ds = vector("v", provenance={"ogc_publication": {"filter": {"level": "synthetic_town", "nope": 1, "name": ["x"]}}})
    assert vector_static_filter(ds, COLUMNS) == {"level": "synthetic_town"}


@pytest.mark.parametrize("provenance", [{}, {"ogc_publication": {"filter": "level = 1"}}, {"ogc_publication": None}])
def test_static_filter_absent(provenance):
    assert vector_static_filter(vector("v", provenance=provenance), COLUMNS) == {}


def test_dump_has_no_secrets_and_keeps_env_refs(settings):
    text = dump_config(build_config(BASE, [vector("v"), raster("r")], COLUMNS, settings))
    assert text.startswith("# GENERATED")
    for ref in ("${POSTGRES_HOST}", "${POSTGRES_PASSWORD}", "${OGC_PUBLIC_URL:-"):
        assert f": {ref}" in text  # plain (unquoted) scalars, so pygeoapi substitutes them
    assert yaml.safe_load(text)["resources"]["r"]["providers"][0]["data"] == "r"


@pytest.mark.parametrize("render,expected", [
    ({"rescale": [0, 2]}, [0.0, 2.0]),
    ({"rescale": [2, 1]}, None),
    ({"rescale": "0,2"}, None),
    ({"rescale": [0, True]}, None),
    ({}, None),
])
def test_raster_rescale_hint(settings, render, expected):
    ds = raster("r", provenance={"ogc_publication": {"render": render}})
    options = build_config(BASE, [ds], COLUMNS, settings)["resources"]["r"]["providers"][0]["options"]
    assert options.get("rescale") == expected


def test_unknown_tile_scheme_rejected():
    with pytest.raises(ValueError):
        PublishSettings(env={"OGC_TILE_SCHEMES": "WebMercatorQuad,NoSuchQuad"})
