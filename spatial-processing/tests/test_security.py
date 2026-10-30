"""Path safety, hostile catalog rows, output cleanup and filename safety."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from spatial_processing.errors import ApiError
from spatial_processing.outputs import OutputManager
from spatial_processing.paths import resolve_asset_path, safe_filename_component
from synthetic import TOWN_A


@pytest.mark.parametrize(
    "dataset_id",
    ["evil-traversal", "evil-absolute", "evil-url", "evil-vsicurl", "evil-symlink", "evil-vrt", "evil-null"],
)
@pytest.mark.parametrize("route", ["/operations/zonal-statistics", "/operations/clip"])
def test_hostile_catalog_paths_are_never_read(client, outside_file, dataset_id, route):
    r = client.post(route, json={"dataset_id": dataset_id, "aoi": TOWN_A, "aoi_crs": "EPSG:4326"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "ASSET_PATH_REJECTED"
    # no server paths or secret values leak to the client
    for leaked in (str(outside_file), "secret", "example.com", "vsicurl", "private", "4200"):
        assert leaked not in r.text


def test_missing_and_corrupted_assets(client):
    r = client.post("/operations/zonal-statistics", json={"dataset_id": "missing-file", "aoi": TOWN_A, "aoi_crs": "EPSG:4326"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "ASSET_MISSING"
    assert "does_not_exist" not in r.text
    r = client.post("/operations/zonal-statistics", json={"dataset_id": "corrupted", "aoi": TOWN_A, "aoi_crs": "EPSG:4326"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "ASSET_UNREADABLE"


@pytest.mark.parametrize(
    "asset_path",
    ["../x.tif", "a/../../x.tif", "/etc/passwd", "http://h/x.tif", "s3://b/x.tif", "/vsizip/x.zip/a.tif",
     "x.vrt", "x.tif\x00.png", "a\\..\\x.tif", "", "./", "/data/assets/../etc/x.tif"],
)
def test_resolve_asset_path_rejects(tmp_path, asset_path):
    with pytest.raises(ApiError) as info:
        resolve_asset_path(tmp_path, asset_path)
    assert info.value.code in ("ASSET_PATH_REJECTED", "ASSET_MISSING")
    assert info.value.code == "ASSET_PATH_REJECTED" or asset_path.endswith(".tif")


def test_resolve_asset_path_symlink_inside_root_is_allowed(tmp_path):
    (tmp_path / "real.tif").write_bytes(b"x")
    (tmp_path / "alias.tif").symlink_to(tmp_path / "real.tif")
    assert resolve_asset_path(tmp_path, "alias.tif") == (tmp_path / "real.tif").resolve()


def test_resolve_asset_path_directory_rejected(tmp_path):
    (tmp_path / "dir.tif").mkdir()
    with pytest.raises(ApiError) as info:
        resolve_asset_path(tmp_path, "dir.tif")
    assert info.value.code == "ASSET_PATH_REJECTED"


@pytest.mark.parametrize(
    "text, expected",
    [("synthetic-worldpop-2001", "synthetic-worldpop-2001"), ("../../etc/passwd", "etc-passwd"),
     ("a b;c|d", "a-b-c-d"), ("...", "dataset"), ("x" * 200, "x" * 64)],
)
def test_safe_filename_component(text, expected):
    assert safe_filename_component(text) == expected


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old), follow_symlinks=False)


def test_cleanup_only_removes_expired_uuid_dirs(tmp_path):
    root = tmp_path / "outputs"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    root.mkdir()
    mgr = OutputManager(root)

    old_job = root / str(uuid.uuid4())
    old_job.mkdir()
    (old_job / "clip.tif").write_bytes(b"x")
    (old_job / "link-out").symlink_to(outside / "keep.txt")
    _age(old_job, 10_000)

    fresh_job = root / str(uuid.uuid4())
    fresh_job.mkdir()
    (fresh_job / "clip.tif").write_bytes(b"x")

    active_job = root / str(uuid.uuid4())
    active_job.mkdir()
    _age(active_job, 10_000)

    not_uuid = root / "important"
    not_uuid.mkdir()
    _age(not_uuid, 10_000)

    symlinked_dir = root / str(uuid.uuid4())
    symlinked_dir.symlink_to(outside, target_is_directory=True)

    nested = root / str(uuid.uuid4())
    (nested / "sub").mkdir(parents=True)
    _age(nested, 10_000)

    removed = mgr.cleanup(ttl_seconds=3600, active=[uuid.UUID(active_job.name)])

    assert removed == [old_job.name]
    assert not old_job.exists()
    assert fresh_job.exists() and active_job.exists() and not_uuid.exists() and nested.exists()
    assert symlinked_dir.is_symlink()
    assert (outside / "keep.txt").read_text() == "precious"


def test_resolve_result_rejects_unsafe_stored_paths(tmp_path):
    mgr = OutputManager(tmp_path)
    (tmp_path.parent / "elsewhere.tif").write_bytes(b"x")
    for stored in ("../elsewhere.tif", "/etc/passwd", None, "", "missing/x.tif"):
        with pytest.raises(ApiError) as info:
            mgr.resolve_result(stored)
        assert info.value.status == 410
