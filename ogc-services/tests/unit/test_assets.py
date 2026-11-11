# Unit tests: path safety of the tile service's dataset resolver (cgis_tiler.assets).

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from cgis_tiler.assets import (
    AssetResolver,
    AssetUnavailable,
    DatasetNotFound,
    RasterRecord,
    check_driver,
    confine_asset_path,
    validate_dataset_id,
)


# RasterRecord with the given asset_path.
def record(asset_path, dataset_id="ds-1"):
    return RasterRecord(dataset_id, 1, "t", asset_path, "EPSG:4326", -9999.0, "people_per_cell", 2001, None)


# Writes a small GeoTIFF (or other driver) for path tests.
def write_tif(path: Path, driver="GTiff"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver=driver, width=8, height=8, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(78, 18, 0.001, 0.001)) as dst:
        dst.write(np.ones((8, 8), dtype="float32"), 1)


# Asset root with one valid file plus a file outside the root.
@pytest.fixture
def root(tmp_path):
    assets = tmp_path / "assets"
    write_tif(assets / "ok" / "a.tif")
    write_tif(tmp_path / "outside.tif")
    return assets


# Well-formed dataset_ids are accepted.
@pytest.mark.parametrize("dataset_id", ["synthetic-worldpop-2001", "a", "A.b_c-1"])
def test_valid_ids(dataset_id):
    assert validate_dataset_id(dataset_id) == dataset_id


# Malformed dataset_ids raise DatasetNotFound.
@pytest.mark.parametrize("dataset_id", ["", "../x", "a/b", "-a", ".hidden", "a b", "a%2Fb", "x" * 129, "é"])
def test_invalid_ids_rejected(dataset_id):
    with pytest.raises(DatasetNotFound):
        validate_dataset_id(dataset_id)


# A relative asset_path resolves inside the root.
def test_relative_path_inside_root(root):
    assert confine_asset_path(root, "ok/a.tif") == (root / "ok" / "a.tif").resolve()


# An absolute asset_path inside the root is accepted.
def test_absolute_path_inside_root_tolerated(root):
    assert confine_asset_path(root, str(root / "ok" / "a.tif")) == (root / "ok" / "a.tif").resolve()


@pytest.mark.parametrize("asset_path", [
    "../outside.tif",
    "ok/../../outside.tif",
    "/etc/passwd.tif",
    "http://example.com/x.tif",
    "/vsicurl/http://example.com/x.tif",
    "/vsizip/ok/a.zip/a.tif",
    "ok\\a.tif",
    "ok/a.tif\x00.png",
    "ok/a.vrt",
    "ok/missing.tif",
    "ok",
    "",
    None,
])
# Traversal, absolute-outside, URL, /vsi*, bad chars/suffix and missing paths are refused.
def test_unsafe_paths_rejected(root, asset_path):
    with pytest.raises(ValueError):
        confine_asset_path(root, asset_path)


# A symlink pointing outside the root is refused.
def test_symlink_escaping_root_rejected(root, tmp_path):
    os.symlink(tmp_path / "outside.tif", root / "ok" / "link.tif")
    with pytest.raises(ValueError, match="escapes"):
        confine_asset_path(root, "ok/link.tif")


# A symlink inside the root is followed.
def test_symlink_within_root_allowed(root):
    os.symlink(root / "ok" / "a.tif", root / "ok" / "alias.tif")
    assert confine_asset_path(root, "ok/alias.tif") == (root / "ok" / "a.tif").resolve()


# A VRT with a .tif suffix fails the driver check; a GeoTIFF passes.
def test_driver_check_rejects_disguised_vrt(root):
    vrt = root / "ok" / "disguised.tif"
    vrt.write_text(f'<VRTDataset rasterXSize="8" rasterYSize="8"><VRTRasterBand dataType="Float32" band="1">'
                   f'<SimpleSource><SourceFilename>{root / "ok" / "a.tif"}</SourceFilename></SimpleSource>'
                   f"</VRTRasterBand></VRTDataset>")
    with pytest.raises(ValueError, match="driver"):
        check_driver(vrt)
    check_driver(root / "ok" / "a.tif")


# Unknown dataset -> DatasetNotFound.
def test_resolver_unknown_dataset(root):
    resolver = AssetResolver(root, lambda _id: None)
    with pytest.raises(DatasetNotFound):
        resolver.resolve("nope")


# Unsafe asset -> AssetUnavailable whose message reveals no path.
def test_resolver_refuses_bad_asset_without_leaking_path(root):
    resolver = AssetResolver(root, lambda _id: record("../outside.tif"))
    with pytest.raises(AssetUnavailable) as err:
        resolver.resolve("ds-1")
    assert "outside" not in err.value.message and "/" not in err.value.message


# Invalid ids are rejected before any catalog lookup.
def test_resolver_does_not_query_catalog_for_invalid_id(root):
    calls = []
    resolver = AssetResolver(root, lambda i: calls.append(i))
    with pytest.raises(DatasetNotFound):
        resolver.resolve("../../etc/passwd")
    assert calls == []


# Successful resolutions are cached until clear().
def test_resolver_caches_successful_lookups(root):
    calls = []

    # Catalog lookup stub that records calls.
    def lookup(dataset_id):
        calls.append(dataset_id)
        return record("ok/a.tif", dataset_id)

    resolver = AssetResolver(root, lookup, ttl_seconds=60)
    first = resolver.resolve("ds-1")
    second = resolver.resolve("ds-1")
    assert first == second and calls == ["ds-1"]
    resolver.clear()
    resolver.resolve("ds-1")
    assert calls == ["ds-1", "ds-1"]
