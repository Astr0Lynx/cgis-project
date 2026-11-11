# Ingest worker: validate and publish admin uploads with the existing importer (ingestion/), on the ingestion
# pins, assets read-write, DB role cgis_app_worker (member of cgis_ingest). Cheap pre-checks refuse
# decompression bombs (TIFF header size/bands/ratio), non-GTiff drivers and over-nested GeoJSON first.

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from .. import storage
from ..config import Settings
from ..storage import StorageError
from .runner import JobContext, JobFailure, Outcome

log = logging.getLogger("cgis_app.worker.ingest")


# Pre-check failure with an importer-style code. Converted to a job error by _validate/_publish.
class PrecheckError(Exception):
    # Store code, message and details.
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# Header-only raster guards (bands, cells, uncompressed size, compression ratio); returns warnings.
# Used by _validate and _publish.
def precheck_raster(path: Path, settings: Settings) -> list[str]:
    import numpy as np
    import rasterio
    from rasterio.errors import RasterioError

    warnings: list[str] = []
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_CACHEMAX=settings.gdal_cachemax_mb), \
                rasterio.open(path, driver="GTiff") as src:
            if src.driver != "GTiff":
                raise PrecheckError("UNSUPPORTED_FORMAT", "only GeoTIFF/COG rasters are accepted")
            pixels = src.width * src.height
            itemsize = max(np.dtype(dt).itemsize for dt in src.dtypes) if src.dtypes else 1
            uncompressed = pixels * src.count * itemsize
            details = {"width": src.width, "height": src.height, "bands": src.count, "uncompressed_bytes": uncompressed}
            if src.count > settings.max_raster_bands:
                raise PrecheckError("TOO_MANY_BANDS", f"the raster has {src.count} bands (limit "
                                                      f"{settings.max_raster_bands})", details)
            if pixels > settings.max_raster_pixels:
                raise PrecheckError("RASTER_TOO_LARGE", f"the raster has {pixels} cells (limit "
                                                        f"{settings.max_raster_pixels})", details)
            if uncompressed > settings.max_raster_uncompressed_bytes:
                raise PrecheckError("RASTER_TOO_LARGE", f"the raster would decompress to {uncompressed} bytes (limit "
                                                        f"{settings.max_raster_uncompressed_bytes})", details)
            size = max(1, path.stat().st_size)
            ratio = uncompressed / size
            if ratio > settings.max_compression_ratio:
                raise PrecheckError("SUSPICIOUS_COMPRESSION",
                                    f"the raster decompresses {ratio:.0f}x (limit {settings.max_compression_ratio:.0f}x);"
                                    " refusing a possible decompression bomb", {**details, "ratio": round(ratio, 1)})
            if src.count > 1:
                warnings.append(f"the file has {src.count} bands; only band 1 (or metadata.band) is imported")
            if src.crs is not None and src.crs.is_geographic:
                warnings.append("geographic CRS (degrees): pixel area varies with latitude; units must be per cell, "
                                "not per area, to be summed")
    except PrecheckError:
        raise
    except RasterioError as exc:
        raise PrecheckError("CORRUPT_RASTER", f"the file cannot be opened as a GeoTIFF: {str(exc)[:200]}") from None
    return warnings


# Parse the GeoJSON (bounded nesting) and require a non-empty FeatureCollection. Used by _validate/_publish.
def precheck_vector(path: Path, settings: Settings) -> list[str]:
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))
    except RecursionError:
        raise PrecheckError("CORRUPT_VECTOR", "the GeoJSON is nested too deeply") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrecheckError("CORRUPT_VECTOR", f"the file is not valid UTF-8 JSON: {str(exc)[:200]}") from None
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise PrecheckError("CORRUPT_VECTOR", "the file must be a GeoJSON FeatureCollection")
    features = doc.get("features")
    if not isinstance(features, list) or not features:
        raise PrecheckError("EMPTY_VECTOR", "the FeatureCollection has no features")
    if len(features) > 1_000_000:
        raise PrecheckError("VECTOR_TOO_LARGE", "more than 1,000,000 features")
    return []


# The job's upload row (fails when discarded). Used by handle.
def _upload(ctx: JobContext) -> dict:
    row = ctx.conn.execute("SELECT * FROM cgis_app.uploads WHERE upload_id = %s", (ctx.job["upload_id"],)).fetchone()
    if row is None or row["status"] == "discarded":
        raise JobFailure("INVALID_STATE", "the upload was discarded")
    return row


# Importer DatasetSpec for an upload, file resolved inside the staging root. Used by _validate/_publish.
def _spec(upload: dict, staging_root: Path):
    from ingestion.manifest import parse_entry

    entry = dict(upload["metadata"])
    entry["file"] = upload["staged_name"]  # resolved against the staging root, never a client path
    return parse_entry(entry, staging_root)


# Job error dict with an upper-case code. Used by _validate/_publish.
def _error_dict(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code.upper(), "message": message}
    if details:
        err["details"] = details
    return err


# Mark the upload failed with its validation errors. Used by _validate/_publish.
def _fail_upload(ctx: JobContext, upload: dict, error: dict, warnings: list[str]) -> None:
    ctx.conn.execute(
        "UPDATE cgis_app.uploads SET status = 'failed', error = %s, validation = %s WHERE upload_id = %s "
        "AND status IN ('validating', 'publishing')",
        (Jsonb(error), Jsonb({"ok": False, "errors": [error], "warnings": warnings}), upload["upload_id"]))


# Pre-check + Importer.validate; upload becomes validated or failed. Used by handle (upload/validate jobs).
def _validate(ctx: JobContext, upload: dict) -> Outcome:
    from ingestion import IMPORTER_NAME, __version__ as importer_version
    from ingestion.errors import IngestionError
    from ingestion.importer import Importer

    settings = ctx.settings
    warnings = list(((upload.get("validation") or {}).get("warnings")) or [])
    try:
        ctx.progress(0.05, "checking the file")
        path = storage.staged_path(settings.staging_root, upload["staged_name"])
        warnings += precheck_raster(path, settings) if upload["kind"] == "raster" else precheck_vector(path, settings)
        ctx.progress(0.15, "validating with the importer")
        spec = _spec(upload, settings.staging_root)
        result = Importer(None, settings.asset_root).validate(spec)
        cat = ctx.conn.execute("SELECT status, checksum_sha256 FROM catalog.datasets WHERE dataset_id = %s AND "
                               "version = %s", (upload["dataset_id"], upload["version"])).fetchone()
        if cat is not None and cat["status"] == "ready" and cat["checksum_sha256"] != result.checksum_sha256:
            raise PrecheckError("CHECKSUM_CONFLICT", "this version was published with different content meanwhile; "
                                                     "upload the file as a new version")
        if spec.is_synthetic:
            warnings.append("marked SYNTHETIC: this is test data, not a real population estimate")
    except PrecheckError as exc:
        error = _error_dict(exc.code, exc.message, exc.details)
    except IngestionError as exc:
        error = _error_dict(exc.code, exc.message, exc.details)
    except StorageError as exc:
        error = _error_dict(exc.code, exc.message)
    else:
        validation = {"ok": True, "errors": [], "warnings": warnings, "summary": result.summary,
                      "importer": {"name": IMPORTER_NAME, "version": importer_version},
                      "checksum_sha256": result.checksum_sha256}
        updated = ctx.conn.execute(
            "UPDATE cgis_app.uploads SET status = 'validated', validation = %s, error = NULL WHERE upload_id = %s "
            "AND status = 'validating' RETURNING upload_id", (Jsonb(validation), upload["upload_id"])).fetchone()
        if updated is None:
            raise JobFailure("INVALID_STATE", "the upload changed state during validation")
        return Outcome(result={"outcome": "validated", "upload_id": str(upload["upload_id"]),
                               "validation": validation,
                               "next": f"/admin/datasets/{upload['dataset_id']}/versions/{upload['version']}/publish"})
    _fail_upload(ctx, upload, error, warnings)
    raise JobFailure(error["code"], error["message"], error.get("details"))


# Pre-check + Importer.import_dataset (COG/normalize, catalog ready); upload becomes published or failed.
# Used by handle (publish jobs).
def _publish(ctx: JobContext, upload: dict) -> Outcome:
    from ingestion.catalog import Catalog, connect as ingestion_connect
    from ingestion.errors import IngestionError
    from ingestion.importer import Importer

    settings = ctx.settings
    warnings = list(((upload.get("validation") or {}).get("warnings")) or [])
    conn = None
    try:
        ctx.progress(0.05, "publishing: checking the file")
        path = storage.staged_path(settings.staging_root, upload["staged_name"])
        warnings += precheck_raster(path, settings) if upload["kind"] == "raster" else precheck_vector(path, settings)
        ctx.progress(0.1, "publishing: validating, converting to COG/normalizing and registering in the catalog")
        spec = _spec(upload, settings.staging_root)
        conn = ingestion_connect(ctx.dsn, wait_seconds=10)
        result = Importer(Catalog(conn), settings.asset_root).import_dataset(spec)
    except PrecheckError as exc:
        error = _error_dict(exc.code, exc.message, exc.details)
    except IngestionError as exc:
        error = _error_dict(exc.code, exc.message, exc.details)
    except StorageError as exc:
        error = _error_dict(exc.code, exc.message)
    else:
        did, ver = upload["dataset_id"], upload["version"]
        payload = {"outcome": result.outcome, "dataset_id": did, "version": ver, "kind": result.kind,
                   "checksum_sha256": result.checksum_sha256, "summary": result.summary, "warnings": warnings,
                   "links": {"catalog": f"/api/catalog/datasets/{did}/versions/{ver}", "admin": f"/admin/datasets/{did}",
                             "ogc_collection": f"/ogc/collections/{did}"},
                   "note": "OGC API collections refresh from the catalog every OGC_REFRESH_INTERVAL seconds."}
        ctx.conn.execute("UPDATE cgis_app.uploads SET status = 'published', published_at = now(), result = %s, "
                         "error = NULL WHERE upload_id = %s", (Jsonb(payload), upload["upload_id"]))
        storage.remove_staged(settings.staging_root, upload["staged_name"])
        log.info("published %s@v%s (%s)", did, ver, result.outcome)
        return Outcome(result=payload)
    finally:
        if conn is not None:
            conn.close()
    _fail_upload(ctx, upload, error, warnings)
    raise JobFailure(error["code"], error["message"], error.get("details"))


# Ingest job dispatcher by kind. Used by runner.serve for the ingest queue.
def handle(ctx: JobContext) -> Outcome:
    upload = _upload(ctx)
    kind = ctx.job["kind"]
    if kind in ("upload", "validate"):
        if upload["status"] != "validating":
            raise JobFailure("INVALID_STATE", f"the upload is '{upload['status']}', not 'validating'")
        return _validate(ctx, upload)
    if kind == "publish":
        if upload["status"] != "publishing":
            raise JobFailure("INVALID_STATE", f"the upload is '{upload['status']}', not 'publishing'")
        return _publish(ctx, upload)
    raise JobFailure("INVALID_STATE", f"unknown ingest job kind {kind!r}")


# Fail uploads whose job died, discard stale staging, delete orphan staged files. Run every minute by
# runner.serve.
def maintenance(conn, settings: Settings) -> None:
    # Uploads stuck in validating/publishing whose latest job ended (abandoned after crashes, cancelled).
    conn.execute("""
        UPDATE cgis_app.uploads u SET status = 'failed',
               error = coalesce(j.error, jsonb_build_object('code', 'JOB_ABANDONED', 'message', 'the job did not finish'))
          FROM (SELECT DISTINCT ON (upload_id) upload_id, status, error FROM cgis_app.jobs
                 WHERE upload_id IS NOT NULL ORDER BY upload_id, created_at DESC) j
         WHERE j.upload_id = u.upload_id AND u.status IN ('validating', 'publishing')
           AND j.status IN ('failed', 'cancelled', 'expired')
    """)
    stale = conn.execute(
        "UPDATE cgis_app.uploads SET status = 'discarded', result = jsonb_build_object('discarded', 'staging retention "
        "elapsed') WHERE status IN ('staged', 'validated', 'failed') AND updated_at < now() - make_interval(secs => %s) "
        "RETURNING staged_name", (settings.staging_ttl_seconds,)).fetchall()
    for r in stale:
        storage.remove_staged(settings.staging_root, r["staged_name"])
    live = {r["staged_name"] for r in conn.execute(
        "SELECT staged_name FROM cgis_app.uploads WHERE status NOT IN ('discarded', 'published')").fetchall()}
    root = Path(os.path.realpath(settings.staging_root))
    if not root.is_dir():
        return
    now = time.time()
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False) or not storage.STAGED_NAME_RE.match(entry.name):
                continue
            if entry.name in live or now - entry.stat(follow_symlinks=False).st_mtime < 3600:
                continue  # an upload may be streaming right now
            os.unlink(entry.path)
            log.info("removed orphan staged file %s", entry.name)
