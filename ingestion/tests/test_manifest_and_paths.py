from __future__ import annotations

import json

import pytest

from ingestion.errors import AssetError, ManifestError
from ingestion.manifest import load_manifest, parse_entry
from ingestion.paths import asset_relpath, resolve_under

from .conftest import entry


def test_contract_example_shape_is_accepted(fixture_dir):
    raw = {"dataset_id": "synthetic-worldpop-2001", "version": 1, "title": "SYNTHETIC population raster (2001)",
           "kind": "raster", "product_level": "source", "year": 2001, "units": "people_per_cell",
           "source": "deterministic-fixture", "file": str(fixture_dir / "synthetic_2001.tif")}
    s = parse_entry(raw, fixture_dir)
    assert s.dataset_id == "synthetic-worldpop-2001" and s.band == 1 and s.is_synthetic


@pytest.mark.parametrize("shape_", ["single", "list", "datasets"])
def test_manifest_shapes(tmp_path, fixture_dir, shape_):
    e = entry(fixture_dir, "synthetic-worldpop-2001", file=str(fixture_dir / "synthetic_2001.tif"))
    doc = {"single": e, "list": [e], "datasets": {"datasets": [e]}}[shape_]
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    base, entries = load_manifest(p)
    assert base == tmp_path and len(entries) == 1


def test_relative_file_resolves_against_manifest_dir(fixture_dir):
    base, entries = load_manifest(fixture_dir / "manifest.json")
    s = parse_entry(entries[0], base)
    assert s.file == fixture_dir / "synthetic_2001.tif"


@pytest.mark.parametrize("override,code", [
    ({"kind": "pointcloud"}, "unsupported_kind"),
    ({"product_level": "level9"}, "invalid_product_level"),
    ({"dataset_id": "Bad ID"}, "invalid_dataset_id"),
    ({"dataset_id": "../escape"}, "invalid_dataset_id"),
    ({"version": 0}, "invalid_field"),
    ({"version": True}, "invalid_field"),
    ({"version": None}, "missing_field"),
    ({"title": ""}, "missing_field"),
    ({"units": None}, "missing_field"),
    ({"file": "does_not_exist.tif"}, "file_not_found"),
    ({"title": "population raster"}, "synthetic_not_marked"),
    ({"checksum_sha256": "abc"}, "invalid_field"),
    ({"expect": {"colour": 1}}, "invalid_field"),
    ({"expect": {"bounds": [1, 2]}}, "invalid_field"),
    ({"provenance": []}, "invalid_field"),
    ({"year": "2001"}, "invalid_field"),
])
def test_bad_entries_have_clear_codes(fixture_dir, override, code):
    with pytest.raises(ManifestError) as ei:
        parse_entry(entry(fixture_dir, "synthetic-worldpop-2001", **override), fixture_dir)
    assert ei.value.code == code


def test_manifest_file_errors(tmp_path):
    with pytest.raises(ManifestError) as ei:
        load_manifest(tmp_path / "missing.json")
    assert ei.value.code == "manifest_not_found"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ManifestError) as ei:
        load_manifest(bad)
    assert ei.value.code == "manifest_not_json"
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps([{"dataset_id": "a", "version": 1}, {"dataset_id": "a", "version": 1}]))
    with pytest.raises(ManifestError) as ei:
        load_manifest(dup)
    assert ei.value.code == "manifest_duplicate"


def test_unknown_keys_are_kept_not_fatal(fixture_dir):
    s = parse_entry(entry(fixture_dir, "synthetic-worldpop-2001", crs="EPSG:4326"), fixture_dir)
    assert s.extra == {"crs": "EPSG:4326"}


def test_asset_layout_and_path_safety(tmp_path):
    rel = asset_relpath("raster", "synthetic-worldpop-2001", 3)
    assert str(rel) == "rasters/synthetic-worldpop-2001/v3/synthetic-worldpop-2001_v3.tif"
    assert resolve_under(tmp_path, rel) == tmp_path / rel
    for bad in ("/etc/passwd", "../x.tif", "a/../../x.tif", ""):
        with pytest.raises(AssetError):
            resolve_under(tmp_path, bad)
    (tmp_path / "link").symlink_to("/etc")
    with pytest.raises(AssetError):
        resolve_under(tmp_path, "link/passwd")
