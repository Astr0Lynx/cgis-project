from __future__ import annotations

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.transform import from_origin
from rio_cogeo.cogeo import cog_validate

from ingestion.errors import ValidationError
from ingestion.manifest import parse_entry
from ingestion.raster import inspect_raster, verify_cog, write_cog

from .conftest import raster_entry, spec, write_raster


def _spec(path, **kw):
    return parse_entry(raster_entry(path, **kw), path.parent)


def test_fixture_inspection(fixture_dir):
    info = inspect_raster(spec(fixture_dir, "synthetic-worldpop-2001"))
    assert info.crs == "EPSG:4326" and (info.width, info.height) == (64, 64)
    assert info.nodata == -9999.0 and info.dtype == "float32" and info.mask == "nodata"
    assert info.bbox_wgs84 == pytest.approx([78.0, 17.936, 78.064, 18.0])
    assert info.resolution_x == pytest.approx(0.001) and info.resolution_y == pytest.approx(0.001)
    assert info.stats["valid_count"] == 3600 and info.stats["sum"] == 3600.0
    assert info.stats["nodata_count"] == 64 * 64 - 3600
    assert info.source_units == "people_per_cell" and info.source_is_cog is False


def test_cog_roundtrip_preserves_everything(fixture_dir, tmp_path):
    s = spec(fixture_dir, "synthetic-worldpop-2011")
    info = inspect_raster(s)
    out = tmp_path / "out.tif"
    write_cog(s, info, out, {"SYNTHETIC": "TRUE"})
    details = verify_cog(s, info, out)
    assert cog_validate(str(out), quiet=True)[0]
    assert details["format"] == "COG" and details["compression"] == "DEFLATE"
    with rasterio.open(fixture_dir / "synthetic_2011.tif") as a, rasterio.open(out) as b:
        assert b.crs == a.crs and b.transform == a.transform and b.nodata == a.nodata
        assert np.array_equal(a.read(1), b.read(1))
        assert b.units == ("people_per_cell",) and b.tags()["SYNTHETIC"] == "TRUE"
        assert b.compression.value.upper() == "DEFLATE"
    assert not list(tmp_path.glob("*.intermediate.tif"))


def test_large_raster_gets_tiles_and_overviews(tmp_path):
    data = np.arange(2048 * 2048, dtype=np.int32).reshape(2048, 2048) % 1000
    data[0, :] = -1
    p = write_raster(tmp_path / "big.tif", data, nodata=-1)
    s = _spec(p)
    info = inspect_raster(s)
    out = tmp_path / "big_cog.tif"
    write_cog(s, info, out, {})
    details = verify_cog(s, info, out)
    assert details["blocksize"] == [512, 512] and details["overview_factors"] == [2, 4]
    assert info.nodata == -1 and isinstance(info.nodata, int)


def test_projected_crs_different_resolution_and_multiband(tmp_path):
    """No synthetic assumptions: UTM 44N, 100 m, uint16, band 2 of 3, offset origin."""
    arr = np.stack([np.full((50, 80), 7, np.uint16), np.full((50, 80), 9, np.uint16),
                    np.zeros((50, 80), np.uint16)])
    arr[1, :5, :] = 65535
    p = write_raster(tmp_path / "utm.tif", arr, crs="EPSG:32644",
                     transform=from_origin(500000, 2000000, 100, 100), nodata=65535)
    s = _spec(p, band=2, year=2030, units="people_per_hectare",
              expect={"crs": "EPSG:32644", "resolution": [100, 100], "band_count": 3, "dtype": "uint16"})
    info = inspect_raster(s)
    assert info.crs == "EPSG:32644" and info.resolution_x == 100 and info.band == 2
    assert info.stats["valid_count"] == 45 * 80 and info.stats["sum"] == 45 * 80 * 9
    w, south, e, n = info.bbox_wgs84
    assert 80.9 < w < 81.1 and 17.9 < south < 18.1 and w < e and south < n
    out = tmp_path / "utm_cog.tif"
    write_cog(s, info, out, {})
    verify_cog(s, info, out)
    with rasterio.open(out) as c:
        assert c.count == 1 and c.crs.to_epsg() == 32644 and (c.read(1)[10] == 9).all()


def test_rejects_missing_crs(tmp_path):
    p = write_raster(tmp_path / "nocrs.tif", np.ones((8, 8), np.float32), crs=None)
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "missing_crs"


def test_rejects_missing_transform(tmp_path):
    p = write_raster(tmp_path / "nogt.tif", np.ones((8, 8), np.float32), transform=Affine.identity())
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "missing_transform"


def test_rejects_rotated_transform(tmp_path):
    p = write_raster(tmp_path / "rot.tif", np.ones((8, 8), np.float32),
                     transform=Affine(0.001, 0.0002, 78.0, 0.0002, -0.001, 18.0))
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "rotated_transform"


def test_rejects_missing_nodata_unless_allowed(tmp_path):
    p = write_raster(tmp_path / "nond.tif", np.ones((8, 8), np.float32), nodata=None)
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "missing_nodata"
    assert inspect_raster(_spec(p, allow_missing_nodata=True)).mask == "none"


def test_rejects_undeclared_nan(tmp_path):
    a = np.ones((8, 8), np.float32)
    a[3, 3] = np.nan
    p = write_raster(tmp_path / "nan.tif", a)
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "invalid_values"


def test_rejects_all_nodata(tmp_path):
    p = write_raster(tmp_path / "empty.tif", np.full((8, 8), -9999, np.float32))
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "empty_raster"


def test_rejects_missing_band(fixture_dir):
    with pytest.raises(ValidationError) as ei:
        inspect_raster(spec(fixture_dir, "synthetic-worldpop-2001", band=2))
    assert ei.value.code == "missing_band"


@pytest.mark.parametrize("content", [b"", b"this is not a tiff"])
def test_rejects_corrupt_files(tmp_path, content):
    p = tmp_path / "corrupt.tif"
    p.write_bytes(content)
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code == "corrupt_raster"


def test_rejects_truncated_geotiffs(tmp_path, fixture_dir):
    # Small fixture: pixel strips precede the IFD, so truncation loses the GeoKeys.
    data = (fixture_dir / "synthetic_2001.tif").read_bytes()
    p = tmp_path / "trunc_small.tif"
    p.write_bytes(data[: len(data) // 2])
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p))
    assert ei.value.code in ("corrupt_raster", "missing_crs")
    # Larger tiled raster: IFD first, tile data cut off -> unreadable pixels.
    big = write_raster(tmp_path / "big.tif", np.random.default_rng(0).random((1024, 1024), dtype=np.float32),
                       tiled=True, blockxsize=256, blockysize=256, compress="deflate")
    full = big.read_bytes()
    p2 = tmp_path / "trunc_big.tif"
    p2.write_bytes(full[: len(full) // 2])
    with pytest.raises(ValidationError) as ei:
        inspect_raster(_spec(p2))
    assert ei.value.code == "corrupt_raster"


def test_expectation_mismatch_is_reported(fixture_dir):
    with pytest.raises(ValidationError) as ei:
        inspect_raster(spec(fixture_dir, "synthetic-worldpop-2001",
                            expect={"crs": "EPSG:32644", "width": 65, "nodata": 0}))
    assert ei.value.code == "expectation_mismatch"
    assert {m["field"] for m in ei.value.details["mismatches"]} == {"crs", "width", "nodata"}


def test_input_cog_is_detected(fixture_dir, tmp_path):
    s = spec(fixture_dir, "synthetic-worldpop-2001")
    out = tmp_path / "already_cog.tif"
    write_cog(s, inspect_raster(s), out, {})
    assert inspect_raster(_spec(out, units="people_per_cell")).source_is_cog is True
