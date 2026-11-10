"""Export worker: reprojected/converted full exports and clips, written to the export volume.

* Raster full + reprojection: a ``WarpedVRT`` (nearest neighbour) is read in full-width strips of at most
  ``CHUNK_PIXELS`` cells and written to a tiled DEFLATE GeoTIFF, so memory stays bounded for national
  rasters; ``COG`` output is then produced by GDAL's COG driver from that file. The grid size is checked
  against ``APP_EXPORT_MAX_PIXELS`` and the growing file against ``APP_EXPORT_MAX_OUTPUT_BYTES``.
* Raster clip: the tested ``/api/operations/clip`` code (pixel-centre rule, NoData kept, optional
  reprojection) bounded by ``APP_EXPORT_CLIP_MAX_PIXELS``.
* Vector: the normalized GeoJSON asset, optionally clipped (features intersecting the AOI, geometries cut
  to it) and/or reprojected.

Outputs are written as ``.tmp-*`` files inside a per-job directory and renamed into place only when
complete and verified; cancelled/failed/interrupted jobs leave nothing behind.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .. import catalog, exportspec, jobs, storage
from ..config import Settings
from ..storage import StorageError
from .runner import JobContext, JobFailure, Outcome

log = logging.getLogger("cgis_app.worker.export")

CHUNK_PIXELS = 8_000_000  # cells per strip (32 MB for float32)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _convert_processing_error(exc) -> JobFailure:
    return JobFailure(exc.code, exc.message, dict(exc.details or {}))


def _crs(text: str | None):
    from pyproj import CRS

    return CRS.from_user_input(text) if text else None


def _epsg_label(crs) -> str:
    epsg = crs.to_epsg() if crs is not None else None
    return f"EPSG:{epsg}" if epsg else (crs.to_string() if crs is not None else "")


# ------------------------------------------------------------------ raster
def _raster_full(ctx: JobContext, row: dict, asset: Path, out_path: Path, fmt: str, out_crs_text: str) -> dict:
    import numpy as np
    import rasterio
    import rasterio.shutil
    from rasterio.crs import CRS as RioCRS
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import calculate_default_transform
    from rasterio.windows import Window

    settings = ctx.settings
    dst_crs = RioCRS.from_user_input(out_crs_text)
    tmp_gtiff = out_path.with_name(f".tmp-{uuid.uuid4().hex}.tif")
    tmp_cog = out_path.with_name(f".tmp-{uuid.uuid4().hex}.cog.tif")
    ctx.cleanup.append(lambda: [p.unlink(missing_ok=True) for p in (tmp_gtiff, tmp_cog)])
    with rasterio.Env(GDAL_CACHEMAX=settings.gdal_cachemax_mb, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_NUM_THREADS="1"), rasterio.open(asset, driver="GTiff") as src:
        transform, width, height = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
        if width * height > settings.export_max_pixels:
            raise JobFailure("OUTPUT_TOO_LARGE", f"the reprojected grid has {width * height} cells (limit "
                                                 f"{settings.export_max_pixels})")
        dtype = src.dtypes[0]
        nodata = src.nodata
        predictor = 3 if np.issubdtype(np.dtype(dtype), np.floating) else 2
        profile = {"driver": "GTiff", "width": width, "height": height, "count": 1, "dtype": dtype, "crs": dst_crs,
                   "transform": transform, "nodata": nodata, "tiled": True, "blockxsize": 512, "blockysize": 512,
                   "compress": "DEFLATE", "predictor": predictor, "BIGTIFF": "IF_SAFER"}
        rows_per_strip = max(512, (CHUNK_PIXELS // max(1, width)) // 512 * 512)
        share = 0.75 if fmt == "COG" else 0.95
        vrt_opts = {"crs": dst_crs, "transform": transform, "width": width, "height": height,
                    "resampling": Resampling.nearest, "warp_mem_limit": 64}
        if nodata is not None:
            vrt_opts.update(src_nodata=nodata, nodata=nodata)
        with WarpedVRT(src, **vrt_opts) as vrt, rasterio.open(tmp_gtiff, "w", **profile) as dst:
            for row0 in range(0, height, rows_per_strip):
                h = min(rows_per_strip, height - row0)
                win = Window(0, row0, width, h)
                dst.write(vrt.read(1, window=win), 1, window=win)
                if nodata is None:
                    dst.write_mask(vrt.read_masks(1, window=win), window=win)
                if tmp_gtiff.stat().st_size > settings.export_max_output_bytes:
                    raise JobFailure("OUTPUT_TOO_LARGE", f"the export exceeds {settings.export_max_output_bytes} bytes")
                ctx.progress(0.05 + share * (row0 + h) / height, f"reprojecting rows {row0 + h}/{height}")
            dst.update_tags(CGIS_DATASET_ID=row["dataset_id"], CGIS_DATASET_VERSION=str(row["version"]),
                            CGIS_UNITS=row.get("units") or "", CGIS_EXPORT_JOB=str(ctx.job["job_id"]),
                            CGIS_RESAMPLING="nearest", CGIS_WARNING=exportspec.RESAMPLING_WARNING)
            if row.get("units"):
                dst.set_band_unit(1, row["units"])
            dst.set_band_description(1, row["dataset_id"])
        if fmt == "COG":
            ctx.progress(0.82, "writing the Cloud-Optimized GeoTIFF")
            rasterio.shutil.copy(str(tmp_gtiff), str(tmp_cog), driver="COG", COMPRESS="DEFLATE", PREDICTOR="YES",
                                 BLOCKSIZE=512, OVERVIEWS="AUTO", RESAMPLING="NEAREST", BIGTIFF="IF_SAFER",
                                 NUM_THREADS="1")
            tmp_gtiff.unlink(missing_ok=True)
            os.replace(tmp_cog, out_path)
        else:
            os.replace(tmp_gtiff, out_path)
    ctx.progress(0.97, "verifying the output")
    return _verify_raster(out_path, fmt, resampled=True)


def _verify_raster(path: Path, fmt: str, *, resampled: bool) -> dict:
    import numpy as np
    import rasterio

    with rasterio.open(path) as chk:
        valid = 0
        total = 0.0
        for _, win in chk.block_windows(1):
            arr = chk.read(1, window=win, masked=True)
            vals = arr.compressed()
            if np.issubdtype(vals.dtype, np.floating):
                vals = vals[np.isfinite(vals)]
            valid += int(vals.size)
            total += float(vals.sum(dtype=np.float64)) if vals.size else 0.0
        layout = chk.tags(ns="IMAGE_STRUCTURE").get("LAYOUT", "GTiff")
        if fmt == "COG" and layout.upper() != "COG":
            raise JobFailure("EXPORT_VERIFICATION_FAILED", "the written file is not a Cloud-Optimized GeoTIFF")
        return {"format": fmt, "layout": layout, "crs": _epsg_label(chk.crs), "width": chk.width, "height": chk.height,
                "transform_gdal": list(chk.transform.to_gdal()), "bounds": list(chk.bounds), "nodata": chk.nodata,
                "dtype": chk.dtypes[0], "count_valid": valid, "sum_valid": total, "resampled": resampled,
                "resampling": "nearest" if resampled else "none"}


def _raster_clip(ctx: JobContext, row: dict, asset: Path, out_path: Path, fmt: str, inp: dict) -> dict:
    from shapely.geometry import shape
    from spatial_processing import __version__ as sp_version
    from spatial_processing.errors import ApiError as ProcessingError
    from spatial_processing.geometry import transform_geometry
    from spatial_processing.raster import gdal_env, open_raster, select_pixels, write_clip

    settings = ctx.settings
    aoi = shape(inp["aoi"])
    aoi_crs = _crs(inp["aoi_crs"])
    out_crs = _crs(inp.get("output_crs"))

    def deadline(stage: str) -> None:
        ctx.check(stage)

    try:
        with gdal_env(settings.gdal_cachemax_mb), open_raster(asset) as src:
            native = transform_geometry(aoi, aoi_crs, _crs(src.crs.to_wkt()))
            ctx.progress(0.2, "selecting pixels (pixel-centre rule)")
            sel = select_pixels(src, native, max_window_pixels=settings.export_clip_max_pixels,
                                catalog_nodata=row.get("nodata"), deadline=deadline)
            if sel.values.size * sel.values.itemsize > settings.export_max_output_bytes:
                raise JobFailure("OUTPUT_TOO_LARGE", f"the clip exceeds {settings.export_max_output_bytes} bytes")
            ctx.progress(0.5, "writing the clipped raster")
            tags = {"DATASET_ID": row["dataset_id"], "DATASET_VERSION": str(row["version"]),
                    "UNITS": row.get("units") or "", "PIXEL_SELECTION": "pixel_center (all_touched=false)",
                    "SOURCE": catalog.public_source(row.get("source")) or "",
                    "PROCESSING": f"cgis-application-api export job {ctx.job['job_id']} (spatial-processing "
                                  f"{sp_version} clip)"}
            meta = write_clip(src, sel, out_path, fmt=fmt, output_crs=out_crs, tags=tags, units=row.get("units"),
                              deadline=deadline)
            # Native-grid figures (exact) next to the written grid's count_valid/sum_valid, which differ when the
            # clip was reprojected (nearest neighbour onto a new grid).
            native_valid = sel.values[sel.valid]
            meta["pixels_in_aoi"] = int(sel.inside.sum())
            meta["native_count_valid"] = int(native_valid.size)
            meta["native_sum_valid"] = float(native_valid.astype("float64").sum()) if native_valid.size else None
            meta["nodata_excluded"] = meta["pixels_in_aoi"] - int(native_valid.size)
            meta["pixel_selection"] = "pixel_center (all_touched=false)"
            meta["count_semantics"] = ("count_valid/sum_valid describe the written file; native_* describe the "
                                       "selected native-grid pixels")
    except ProcessingError as exc:
        raise _convert_processing_error(exc) from None
    return meta


# ------------------------------------------------------------------ vector
def _vector(ctx: JobContext, row: dict, asset: Path, out_path: Path, inp: dict) -> dict:
    import shapely
    from pyproj import CRS, Transformer
    from shapely.geometry import MultiPolygon, Polygon, mapping, shape
    from spatial_processing.geometry import transform_geometry

    limit = 2 * ctx.settings.max_vector_upload_bytes  # the GeoJSON asset is parsed in memory
    if asset.stat().st_size > limit:
        raise JobFailure("OUTPUT_TOO_LARGE", f"the vector dataset is larger than {limit} bytes; use OGC API - "
                                             "Features (paged) for it instead of a single GeoJSON export")
    doc = json.loads(asset.read_bytes().decode("utf-8"))
    features = doc.get("features") or []
    wgs84 = CRS.from_epsg(4326)
    clip_geom = None
    if inp["kind"] == "clip":
        clip_geom = transform_geometry(shape(inp["aoi"]), _crs(inp["aoi_crs"]), wgs84)
        shapely.prepare(clip_geom)
    out_crs = _crs(inp.get("output_crs"))
    transformer = Transformer.from_crs(wgs84, out_crs, always_xy=True) if out_crs is not None else None
    out_features = []
    clipped = 0
    for i, feat in enumerate(features):
        geom = shape(feat["geometry"])
        if clip_geom is not None:
            if not clip_geom.intersects(geom):
                continue
            if not clip_geom.contains(geom):
                cut = geom.intersection(clip_geom)
                polys = [g for g in getattr(cut, "geoms", [cut]) if isinstance(g, (Polygon, MultiPolygon))]
                parts = [p for g in polys for p in getattr(g, "geoms", [g]) if not p.is_empty and p.area > 0]
                if not parts:
                    continue
                geom = MultiPolygon(parts)
                clipped += 1
        if transformer is not None:
            import numpy as np

            geom = shapely.transform(geom, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))
        out_features.append({"type": "Feature", "id": feat.get("id"), "properties": feat.get("properties") or {},
                             "geometry": mapping(geom)})
        if i % 500 == 0:
            ctx.progress(0.1 + 0.8 * (i + 1) / max(1, len(features)), f"features {i + 1}/{len(features)}")
    if not out_features:
        raise JobFailure("NO_FEATURES_SELECTED", "no feature of the dataset intersects the AOI")
    fc: dict[str, Any] = {"type": "FeatureCollection", "name": row["dataset_id"], "dataset_id": row["dataset_id"],
                          "dataset_version": row["version"], "features": out_features}
    warnings = []
    if out_crs is not None:
        fc["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{out_crs.to_epsg()}"}}
        warnings.append(exportspec.GEOJSON_CRS_WARNING)
    tmp = out_path.with_name(f".tmp-{uuid.uuid4().hex}.geojson")
    ctx.cleanup.append(lambda: tmp.unlink(missing_ok=True))
    tmp.write_text(json.dumps(fc, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    if tmp.stat().st_size > ctx.settings.export_max_output_bytes:
        raise JobFailure("OUTPUT_TOO_LARGE", f"the export exceeds {ctx.settings.export_max_output_bytes} bytes")
    os.replace(tmp, out_path)
    bounds = shapely.total_bounds([shape(f["geometry"]) for f in out_features]).tolist()
    return {"format": "GeoJSON", "crs": _epsg_label(out_crs) if out_crs is not None else "EPSG:4326",
            "feature_count": len(out_features), "features_clipped": clipped, "bounds": bounds,
            "rfc7946": out_crs is None, "warnings": warnings,
            "clip_rule": "features intersecting the AOI; geometries cut to the AOI" if clip_geom is not None else None}


# ------------------------------------------------------------------ job entry points
def handle(ctx: JobContext) -> Outcome:
    settings = ctx.settings
    inp = ctx.job["input"]
    job_hex = uuid.UUID(str(ctx.job["job_id"])).hex
    row = catalog.get_version(ctx.conn, inp["dataset_id"], inp["version"])
    if row is None:
        raise JobFailure("DATASET_NOT_READY", f"{inp['dataset_id']} v{inp['version']} is no longer published")
    try:
        asset = storage.resolve_asset(settings.asset_root, row["asset_path"], row["kind"])
        out_dir = storage.make_result_dir(settings.export_root, job_hex)
    except StorageError as exc:
        raise JobFailure("ASSET_UNAVAILABLE" if exc.code.startswith("ASSET") else "STORAGE_ERROR",
                         "the dataset file or the export volume is not available") from None
    ctx.cleanup.append(lambda: storage.remove_result_dir(settings.export_root, job_hex))
    if storage.free_bytes(out_dir) < settings.min_free_bytes:
        raise JobFailure("STORAGE_FULL", "the export volume is almost full")
    fmt = inp["format"]
    filename = exportspec.output_filename(row["dataset_id"], row["version"], inp["kind"], fmt, inp.get("output_crs"),
                                          job_hex)
    out_path = out_dir / filename
    started = time.monotonic()
    ctx.progress(0.02, "starting")
    warnings: list[str] = []
    if row["kind"] == "raster":
        if inp["kind"] == "clip":
            output = _raster_clip(ctx, row, asset, out_path, fmt, inp)
            warnings += output.pop("warnings", [])
        else:
            output = _raster_full(ctx, row, asset, out_path, fmt, inp["output_crs"])
            warnings.append(exportspec.RESAMPLING_WARNING)
        output["units"] = row.get("units")
    else:
        output = _vector(ctx, row, asset, out_path, inp)
        warnings += output.pop("warnings", [])
    ctx.check("checksum")
    size = out_path.stat().st_size
    sha = _sha256(out_path)
    output["size_bytes"] = size
    rec = catalog.public_record(row, include_provenance=False)
    result = {"source": "export_job", "filename": filename, "format": fmt, "media_type": exportspec.MEDIA_TYPES[fmt],
              "size_bytes": size, "sha256": sha, "output": output, "warnings": warnings,
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "provenance": {k: rec[k] for k in ("dataset_id", "version", "title", "source", "license", "citation",
                                                 "synthetic", "units", "checksum_sha256", "product_level", "year")}}
    ctx.cleanup.clear()  # success: keep the output
    return Outcome(result=result, result_path=f"{job_hex}/{filename}", result_size=size, result_sha256=sha,
                   expires_at=jobs.expires_in(settings.export_ttl_seconds))


def maintenance(conn, settings: Settings) -> None:
    """Expire outputs past retention and delete export directories that no live job references."""
    for row in jobs.expire_due(conn, "export"):
        if row.get("result_path"):
            storage.remove_result_dir(settings.export_root, row["result_path"].split("/", 1)[0])
    root = Path(os.path.realpath(settings.export_root))
    if not root.is_dir():
        return
    live = {p.split("/", 1)[0] for p in jobs.live_result_paths(conn)}
    active = {uuid.UUID(str(r["job_id"])).hex for r in conn.execute(
        "SELECT job_id FROM cgis_app.jobs WHERE queue = 'export' AND status IN ('queued', 'running')").fetchall()}
    now = time.time()
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if len(entry.name) != 32 or entry.name in live or entry.name in active:
                continue
            if now - entry.stat(follow_symlinks=False).st_mtime < 600:
                continue
            storage.remove_result_dir(settings.export_root, entry.name)
            log.info("removed orphan export directory %s", entry.name)
