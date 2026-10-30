"""Raster validation, lossless COG conversion and post-conversion verification.

Nothing here assumes the synthetic grid: CRS, transform, resolution, dtype and NoData are read from the
file, and any projected or geographic EPSG CRS with a north-up affine transform is accepted.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import rasterio.shutil
from rasterio.enums import MaskFlags
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.warp import transform_bounds

from .errors import AssetError, ValidationError
from .manifest import DatasetSpec

# Lossless COG creation options (GDAL COG driver). PREDICTOR=YES picks 2 (int) or 3 (float).
# Overviews are for display only; NEAREST never invents values that are not in the data.
COG_OPTIONS = {
    "COMPRESS": "DEFLATE",
    "PREDICTOR": "YES",
    "BLOCKSIZE": "512",
    "OVERVIEWS": "AUTO",
    "RESAMPLING": "NEAREST",
    "BIGTIFF": "IF_SAFER",
}
_INTERMEDIATE_OPTIONS = {"tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate",
                         "bigtiff": "IF_SAFER"}


@dataclass
class RasterInfo:
    width: int
    height: int
    band_count: int
    band: int
    dtype: str
    crs: str
    transform: list[float]
    native_bounds: list[float]
    bbox_wgs84: list[float]
    resolution_x: float
    resolution_y: float
    nodata: float | None
    mask: str
    source_units: str | None
    source_driver: str
    source_is_cog: bool
    stats: dict[str, Any] = field(default_factory=dict)

    def to_provenance(self) -> dict[str, Any]:
        d = asdict(self)
        d["nodata"] = _json_number(self.nodata)
        return d


def _json_number(v: float | None) -> float | str | None:
    if v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    return v


def _open(path: Path):
    try:
        return rasterio.open(path)
    except RasterioError as exc:
        raise ValidationError(f"cannot read raster {path.name}: {exc}", code="corrupt_raster") from exc


def _nodata_equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return a == b


def _is_cog(path: Path) -> bool:
    try:
        from rio_cogeo.cogeo import cog_validate
    except ImportError:  # pragma: no cover - rio-cogeo is a pinned dependency
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            valid, _errors, _warnings = cog_validate(str(path), quiet=True)
    except RasterioError:
        return False
    return bool(valid)


def inspect_raster(spec: DatasetSpec) -> RasterInfo:
    """Fully validate the source raster and return its metadata and value statistics."""
    path = spec.file
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        src = _open(path)
        with src:
            info = _inspect_open(src, spec)
    info.source_is_cog = _is_cog(path)
    _check_expect(info, spec.expect)
    return info


def _inspect_open(src, spec: DatasetSpec) -> RasterInfo:
    name = spec.file.name
    if src.count < 1:
        raise ValidationError(f"{name} has no raster bands", code="missing_band")
    if spec.band > src.count:
        raise ValidationError(f"{name} has {src.count} band(s); manifest requires band {spec.band}",
                              code="missing_band")
    if src.width < 1 or src.height < 1:
        raise ValidationError(f"{name} has empty dimensions", code="invalid_dimensions")

    # Read every pixel first: a truncated file can lose its trailing GeoKeys and would otherwise be
    # misreported as "missing CRS" instead of corrupt.
    idx = spec.band - 1
    raw_nodata = src.nodatavals[idx]
    stats = _band_stats(src, spec.band, raw_nodata, name)

    if src.crs is None:
        raise ValidationError(f"{name} has no CRS; a georeferenced raster is required", code="missing_crs")
    epsg = src.crs.to_epsg()
    if epsg is None:
        raise ValidationError(f"{name} CRS cannot be identified as an EPSG code ({src.crs.to_string()[:80]})",
                              code="unsupported_crs")

    t = src.transform
    if t.is_identity or (src.gcps and src.gcps[0]):
        raise ValidationError(f"{name} has no affine geotransform", code="missing_transform")
    if t.b != 0 or t.d != 0:
        raise ValidationError(f"{name} has a rotated/sheared geotransform; only north-up grids are supported",
                              code="rotated_transform")
    if not (t.a > 0 and t.e < 0):
        raise ValidationError(f"{name} geotransform must have positive x and negative y pixel size",
                              code="invalid_transform")

    dtype = src.dtypes[idx]
    np_dtype = np.dtype(dtype)
    if np_dtype.kind not in "uif":
        raise ValidationError(f"{name} band {spec.band} dtype {dtype} is not supported", code="unsupported_dtype")

    nodata = src.nodatavals[idx]
    flags = src.mask_flag_enums[idx]
    if nodata is not None:
        mask_kind = "nodata"
        if np_dtype.kind in "ui":
            if not float(nodata).is_integer() or not (np.iinfo(np_dtype).min <= nodata <= np.iinfo(np_dtype).max):
                raise ValidationError(f"{name} NoData {nodata} not representable in {dtype}", code="invalid_nodata")
            nodata = int(nodata)
    elif MaskFlags.per_dataset in flags:
        mask_kind = "per_dataset_mask"
    elif MaskFlags.alpha in flags:
        raise ValidationError(f"{name} uses an alpha band mask; supply a NoData value instead", code="missing_nodata")
    elif spec.allow_missing_nodata:
        mask_kind = "none"
    else:
        raise ValidationError(
            f"{name} band {spec.band} declares no NoData value or mask; set NoData in the file "
            "(or allow_missing_nodata=true if every cell is valid)", code="missing_nodata")

    try:
        w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
    except Exception as exc:  # pyproj/GDAL raise various types
        raise ValidationError(f"{name} bounds cannot be transformed to EPSG:4326: {exc}",
                              code="invalid_bounds") from exc
    if not all(math.isfinite(v) for v in (w, s, e, n)) or w < -180.0000001 or e > 180.0000001 \
            or s < -90.0000001 or n > 90.0000001 or w >= e or s >= n:
        raise ValidationError(f"{name} WGS84 bounds {[w, s, e, n]} are not valid lon/lat", code="invalid_bounds")

    units = src.units[idx] if src.units else None

    return RasterInfo(
        width=src.width, height=src.height, band_count=src.count, band=spec.band, dtype=dtype,
        crs=f"EPSG:{epsg}", transform=[t.a, t.b, t.c, t.d, t.e, t.f],
        native_bounds=list(src.bounds), bbox_wgs84=[max(w, -180.0), max(s, -90.0), min(e, 180.0), min(n, 90.0)],
        resolution_x=abs(t.a), resolution_y=abs(t.e), nodata=nodata, mask=mask_kind,
        source_units=units or None, source_driver=src.driver, source_is_cog=False, stats=stats,
    )


def _band_stats(src, band: int, nodata, name: str) -> dict[str, Any]:
    valid = 0
    total = 0
    vmin = math.inf
    vmax = -math.inf
    vsum = 0.0
    is_float = np.dtype(src.dtypes[band - 1]).kind == "f"
    nodata_is_nan = nodata is not None and isinstance(nodata, float) and math.isnan(nodata)
    try:
        for _, win in src.block_windows(band):
            data = src.read(band, window=win, masked=True)
            total += data.size
            vals = data.compressed()
            if is_float and vals.size:
                bad = ~np.isfinite(vals)
                if bad.any() and not nodata_is_nan:
                    raise ValidationError(f"{name} contains NaN/Inf cells that are not declared NoData",
                                          code="invalid_values")
                vals = vals[~bad]
            if vals.size:
                valid += int(vals.size)
                vmin = min(vmin, float(vals.min()))
                vmax = max(vmax, float(vals.max()))
                vsum += float(vals.sum(dtype=np.float64))
    except RasterioError as exc:
        raise ValidationError(f"{name} pixel data is unreadable/corrupt: {exc}", code="corrupt_raster") from exc
    if valid == 0:
        raise ValidationError(f"{name} band {band} has no valid (non-NoData) cells", code="empty_raster")
    return {"valid_count": valid, "nodata_count": total - valid, "min": vmin, "max": vmax, "sum": vsum}


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _check_expect(info: RasterInfo, expect: dict[str, Any]) -> None:
    """Assert optional manifest expectations against the values read from the file."""
    if not expect:
        return
    tol = float(expect.get("tolerance", 1e-9))
    mismatches = []

    def chk(key, ok, actual):
        if not ok:
            mismatches.append({"field": key, "expected": expect[key], "actual": actual})

    if "crs" in expect:
        chk("crs", str(expect["crs"]).upper() == info.crs, info.crs)
    for key, actual in (("width", info.width), ("height", info.height), ("band_count", info.band_count)):
        if key in expect:
            chk(key, expect[key] == actual, actual)
    if "dtype" in expect:
        chk("dtype", expect["dtype"] == info.dtype, info.dtype)
    if "nodata" in expect:
        exp = expect["nodata"]
        exp_f = float(exp) if exp is not None else None
        chk("nodata", _nodata_equal(exp_f, None if info.nodata is None else float(info.nodata)),
            _json_number(info.nodata))
    if "resolution" in expect:
        rx, ry = expect["resolution"]
        chk("resolution", _close(rx, info.resolution_x, tol) and _close(ry, info.resolution_y, tol),
            [info.resolution_x, info.resolution_y])
    if "bounds" in expect:
        chk("bounds", all(_close(a, b, tol) for a, b in zip(expect["bounds"], info.native_bounds)),
            info.native_bounds)
    if mismatches:
        raise ValidationError(f"raster does not match manifest expectations: {[m['field'] for m in mismatches]}",
                              code="expectation_mismatch", details={"mismatches": mismatches})


def write_cog(spec: DatasetSpec, info: RasterInfo, dst: Path, extra_tags: dict[str, str]) -> None:
    """Write the selected band of the source as a lossless COG at ``dst`` (a temp path)."""
    intermediate = dst.with_name(dst.stem + ".intermediate.tif")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with _open(spec.file) as src:
                _write_intermediate(src, spec, info, intermediate, extra_tags)
            rasterio.shutil.copy(str(intermediate), str(dst), driver="COG", **COG_OPTIONS)
    except RasterioError as exc:
        raise AssetError(f"COG conversion failed for {spec.key}: {exc}", code="cog_conversion_failed") from exc
    finally:
        intermediate.unlink(missing_ok=True)


def _write_intermediate(src, spec: DatasetSpec, info: RasterInfo, path: Path, extra_tags: dict[str, str]) -> None:
    band = spec.band
    profile = {
        "driver": "GTiff", "width": src.width, "height": src.height, "count": 1, "dtype": info.dtype,
        "crs": src.crs, "transform": src.transform, "nodata": info.nodata, **_INTERMEDIATE_OPTIONS,
    }
    with rasterio.open(path, "w", **profile) as out:
        out.update_tags(**src.tags())
        out.update_tags(1, **src.tags(band))
        out.update_tags(**extra_tags)
        out.set_band_unit(1, spec.units or "")
        desc = src.descriptions[band - 1]
        if desc:
            out.set_band_description(1, desc)
        out.scales = (src.scales[band - 1],)
        out.offsets = (src.offsets[band - 1],)
        copy_mask = info.mask == "per_dataset_mask"
        for _, win in out.block_windows(1):
            out.write(src.read(band, window=win), 1, window=win)
            if copy_mask:
                out.write_mask(src.read_masks(band, window=win), window=win)


def verify_cog(spec: DatasetSpec, info: RasterInfo, cog_path: Path) -> dict[str, Any]:
    """Prove the COG is valid and retains grid, CRS, NoData, mask, units and every pixel value."""
    try:
        from rio_cogeo.cogeo import cog_validate
    except ImportError as exc:  # pragma: no cover
        raise AssetError("rio-cogeo is required to validate COGs", code="cog_validation_unavailable") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            valid, errors, warns = cog_validate(str(cog_path), quiet=True)
    except RasterioError as exc:
        raise AssetError(f"output for {spec.key} is not a readable raster: {exc}", code="cog_invalid") from exc
    if not valid:
        raise AssetError(f"output for {spec.key} is not a valid COG: {errors}", code="cog_invalid")

    with _open(spec.file) as src, _open(cog_path) as cog:
        problems = []
        if cog.count != 1:
            problems.append("band_count")
        if cog.crs != src.crs:
            problems.append("crs")
        if (cog.width, cog.height) != (src.width, src.height):
            problems.append("dimensions")
        if not cog.transform.almost_equals(src.transform, precision=1e-12):
            problems.append("transform")
        if cog.dtypes[0] != info.dtype:
            problems.append("dtype")
        if not _nodata_equal(cog.nodata, None if info.nodata is None else float(info.nodata)):
            problems.append("nodata")
        if (cog.units[0] or None) != spec.units:
            problems.append("units")
        if problems:
            raise AssetError(f"COG for {spec.key} lost metadata: {problems}", code="cog_verification_failed")
        for _, win in cog.block_windows(1):
            a = src.read(spec.band, window=win)
            b = cog.read(1, window=win)
            if not np.array_equal(a, b, equal_nan=a.dtype.kind == "f"):
                raise AssetError(f"COG for {spec.key} changed pixel values in window {win}",
                                 code="cog_verification_failed")
            if not np.array_equal(src.read_masks(spec.band, window=win), cog.read_masks(1, window=win)):
                raise AssetError(f"COG for {spec.key} changed the valid-data mask in window {win}",
                                 code="cog_verification_failed")
        return {
            "format": "COG", "compression": COG_OPTIONS["COMPRESS"], "predictor": COG_OPTIONS["PREDICTOR"],
            "blocksize": list(cog.block_shapes[0]), "overview_factors": cog.overviews(1),
            "overview_resampling": COG_OPTIONS["RESAMPLING"].lower(), "cog_warnings": warns,
        }
