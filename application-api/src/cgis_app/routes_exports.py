# /api/exports routes for signed-in users. A full native export streams the published asset directly
# (201 completed, Range/ETag/Repr-Digest); reprojections and clips are queued for the export worker (202).
# The processing service's /api/operations and /api/downloads are separate and unchanged.

from __future__ import annotations

import base64
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import catalog, errors, exportspec, geo, jobs, storage, views
from .auth import UserPrincipal
from .errors import ApiError
from .identity import Principal
from .models import ErrorResponse, ExportJob, ExportRequest, Page
from .storage import StorageError

log = logging.getLogger("cgis_app.exports")
router = APIRouter(prefix="/api/exports", tags=["exports"])
_E = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 410, 413, 422, 429, 503, 507)}


# GET /api/exports/capabilities: formats, CRS rules and current limits (public).
@router.get("/capabilities", summary="Supported export kinds, formats per dataset kind and output CRS rules")
def capabilities(request: Request):
    caps = exportspec.capabilities()
    s = request.app.state.settings
    caps["limits"] = {"clip_max_pixels": s.export_clip_max_pixels, "max_output_bytes": s.export_max_output_bytes,
                      "max_pixels": s.export_max_pixels, "max_seconds": s.export_max_seconds,
                      "retention_seconds": s.export_ttl_seconds, "max_active_jobs_per_user": s.export_max_active_per_user,
                      "max_aoi_vertices": s.max_aoi_vertices, "max_aoi_parts": s.max_aoi_parts}
    caps["authentication"] = "required for POST /api/exports and downloads (cookie session, bearer or token)"
    return caps


# Catalog row + access of a dataset version the caller may export (404/409 otherwise). Used by create_export.
def _dataset_for(conn, settings, principal: Principal, dataset_id: str, version: int | None) -> tuple[dict, str]:
    if not catalog.valid_dataset_id(dataset_id):
        raise ApiError(422, errors.INVALID_DATASET_ID, "dataset_id must be a lowercase catalog slug (not a path or URL)")
    access = catalog.access_for(conn, settings, [dataset_id])[dataset_id]
    if not catalog.can_read(access, principal.role):
        raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' was not found")
    row = catalog.get_version(conn, dataset_id, version)
    if row is None:
        if not catalog.dataset_exists(conn, dataset_id):
            raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' was not found")
        if version is None:
            raise ApiError(409, errors.DATASET_NOT_READY, f"dataset '{dataset_id}' has no ready version")
        raise ApiError(404, errors.VERSION_NOT_FOUND, f"dataset '{dataset_id}' has no ready version {version}")
    return row, access


# SHA-256 of the published asset recorded by the importer, if any. Used by _native_result/_public_provenance.
def _asset_sha(row: dict) -> str | None:
    ing = (row.get("provenance") or {}).get("ingestion") or {}
    sha = ing.get("asset_sha256")
    return sha if isinstance(sha, str) and len(sha) == 64 else None


# Provenance block of a native export result. Used by _native_result.
def _public_provenance(row: dict) -> dict[str, Any]:
    rec = catalog.public_record(row, include_provenance=False)
    return {k: rec[k] for k in ("dataset_id", "version", "title", "source", "license", "citation", "synthetic",
                                "units", "checksum_sha256", "product_level", "year")} | {"asset_sha256": _asset_sha(row)}


# Result body, size and checksum of a native (unconverted) download. Used by create_export.
def _native_result(row: dict, fmt: str, settings) -> tuple[dict, int, str | None]:
    try:
        path = storage.resolve_asset(settings.asset_root, row["asset_path"], row["kind"])
    except StorageError as exc:
        log.error("export of %s@v%s: asset problem %s", row["dataset_id"], row["version"], exc.code)
        raise ApiError(503, errors.SERVICE_UNAVAILABLE, "the dataset's file is not available on the server right now")
    size = path.stat().st_size
    prov = row.get("provenance") or {}
    warnings = []
    if row["kind"] == "raster":
        raster = prov.get("raster") or {}
        actual = "COG"
        output = {"crs": row["crs"], "width": raster.get("width"), "height": raster.get("height"),
                  "resolution": [row.get("resolution_x"), row.get("resolution_y")], "nodata": row.get("nodata"),
                  "dtype": raster.get("dtype"), "units": row.get("units"), "resampled": False, "resampling": "none",
                  "bounds_native": raster.get("native_bounds"), "bounds_wgs84": catalog.public_record(row)["bbox"]}
        if fmt == "GTiff":
            warnings.append("The published asset is a lossless Cloud-Optimized GeoTIFF, which is a valid GeoTIFF; it is "
                            "delivered unchanged.")
    else:
        actual = "GeoJSON"
        vec = prov.get("vector") or {}
        output = {"crs": "EPSG:4326", "feature_count": vec.get("feature_count"), "geometry_type": "MultiPolygon",
                  "resampled": False, "bounds_wgs84": catalog.public_record(row)["bbox"]}
    filename = f"{row['dataset_id']}_v{row['version']}.{exportspec.EXTENSIONS[actual]}"
    result = {"source": "native_asset", "filename": filename, "format": actual, "requested_format": fmt,
              "media_type": exportspec.MEDIA_TYPES[actual], "size_bytes": size, "sha256": _asset_sha(row),
              "output": output, "provenance": _public_provenance(row), "warnings": warnings}
    return result, size, _asset_sha(row)


# Cell count of the reprojected grid (None if unknown; 422 if impossible). Used by create_export.
def _estimate_reprojected_pixels(row: dict, dst_crs, settings) -> int | None:
    import rasterio
    from rasterio.warp import calculate_default_transform

    try:
        path = storage.resolve_asset(settings.asset_root, row["asset_path"], "raster")
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(path, driver="GTiff") as src:
            _, w, h = calculate_default_transform(src.crs, dst_crs.to_wkt(), src.width, src.height, *src.bounds)
            return int(w) * int(h)
    except (StorageError, rasterio.errors.RasterioError, ValueError) as exc:
        log.warning("could not estimate reprojected size for %s: %s", row["dataset_id"], type(exc).__name__)
        return None
    except Exception as exc:  # pyproj/GDAL can raise CRSError and others for impossible projections
        raise ApiError(422, errors.UNSUPPORTED_CRS, "the dataset cannot be reprojected to the requested output_crs",
                       {"reason": type(exc).__name__}) from None


# Approximate native cells under an AOI's bounds. Used by create_export (AOI_TOO_LARGE).
def _estimate_clip_pixels(row: dict, geom_native) -> int | None:
    rx, ry = row.get("resolution_x"), row.get("resolution_y")
    if not rx or not ry:
        return None
    minx, miny, maxx, maxy = geom_native.bounds
    return int(((maxx - minx) / rx + 2) * ((maxy - miny) / ry + 2))


# POST /api/exports: validate dataset/format/CRS/AOI and limits, then answer 201 (native) or 202 (queued).
@router.post("", status_code=202, response_model=ExportJob, responses={**_E, 201: {"model": ExportJob, "description":
             "Native full export: completed immediately, download now"}},
             summary="Request a full or clipped export (dataset, version, AOI, output CRS, format)")
def create_export(req: ExportRequest, request: Request, principal: UserPrincipal):
    settings = request.app.state.settings
    with request.app.state.db.connection() as conn:
        row, access = _dataset_for(conn, settings, principal, req.dataset_id, req.version)
        kind = row["kind"]
        fmt = exportspec.normalize_format(kind, req.format)
        if fmt is None:
            raise ApiError(422, errors.UNSUPPORTED_FORMAT,
                           f"format {req.format[:16]!r} is not supported for {kind} datasets",
                           {"supported": exportspec.supported_ids(kind), "dataset_kind": kind})
        native_crs = geo.parse_crs(row["crs"], "dataset crs") if row.get("crs") else None
        out_crs = geo.parse_crs(req.output_crs, "output_crs") if req.output_crs else None
        if out_crs is not None and native_crs is not None and out_crs.equals(native_crs, ignore_axis_order=True):
            out_crs = None  # same as native: exact export
        if kind == "vector" and out_crs is not None and out_crs.to_epsg() == 4326:
            out_crs = None  # vector assets are stored in EPSG:4326
        geom = None
        aoi_crs = None
        if req.kind == "clip":
            if req.aoi is None:
                raise ApiError(422, errors.AOI_REQUIRED, "kind=clip needs an aoi (GeoJSON Polygon/MultiPolygon)")
            aoi_crs = geo.parse_crs(req.aoi_crs or "EPSG:4326", "aoi_crs")
            geom = geo.parse_aoi(req.aoi, aoi_crs, settings)
            from pyproj import CRS
            from shapely.geometry import box

            wgs84 = CRS.from_epsg(4326)
            geom_wgs = geo.transform_geometry(geom, aoi_crs, wgs84)
            bbox = catalog.public_record(row, include_provenance=False)["bbox"]
            if bbox and not geom_wgs.intersects(box(*bbox)):
                raise ApiError(422, errors.AOI_OUTSIDE_DATASET, "the AOI does not overlap the dataset's extent",
                               {"dataset_bbox": bbox})
            if kind == "raster" and native_crs is not None:
                estimate = _estimate_clip_pixels(row, geo.transform_geometry(geom, aoi_crs, native_crs))
                if estimate and estimate > settings.export_clip_max_pixels:
                    raise ApiError(413, errors.AOI_TOO_LARGE,
                                   f"the AOI covers about {estimate} raster cells (limit {settings.export_clip_max_pixels});"
                                   " use a smaller AOI or a full export", {"pixels": estimate,
                                                                           "limit": settings.export_clip_max_pixels})
        elif req.aoi is not None or req.aoi_crs is not None:
            raise ApiError(422, errors.AOI_NOT_ALLOWED, "kind=full exports the whole dataset; remove aoi/aoi_crs or use "
                                                        "kind=clip")

        job_input = {"dataset_id": row["dataset_id"], "version": row["version"], "kind": req.kind, "format": fmt,
                     "output_crs": geo.crs_label(out_crs) if out_crs else None, "dataset_kind": kind,
                     "native_crs": row.get("crs")}
        if geom is not None:
            from shapely.geometry import mapping

            job_input["aoi"] = mapping(geom)
            job_input["aoi_crs"] = geo.crs_label(aoi_crs)

        if req.kind == "full" and out_crs is None:
            result, size, sha = _native_result(row, fmt, settings)
            job = jobs.insert_completed(conn, queue="export", kind="export", owner_id=principal.user_id,
                                        input=job_input, result=result, dataset_id=row["dataset_id"],
                                        dataset_version=row["version"], result_size=size, result_sha256=sha,
                                        expires_at=jobs.expires_in(settings.export_ttl_seconds))
            log.info("export %s native %s@v%s by %s", job["job_id"], row["dataset_id"], row["version"],
                     principal.username)
            return JSONResponse(status_code=201, content=views.export_job(job))

        if kind == "raster" and req.kind == "full" and out_crs is not None:
            pixels = _estimate_reprojected_pixels(row, out_crs, settings)
            if pixels and pixels > settings.export_max_pixels:
                raise ApiError(413, errors.OUTPUT_TOO_LARGE,
                               f"the reprojected raster would have about {pixels} cells (limit "
                               f"{settings.export_max_pixels}); export in the native CRS instead",
                               {"pixels": pixels, "limit": settings.export_max_pixels})
            job_input["estimated_pixels"] = pixels
        if jobs.count_active(conn, owner_id=principal.user_id, queue="export") >= settings.export_max_active_per_user:
            raise ApiError(429, errors.TOO_MANY_JOBS, "you already have the maximum number of running exports; wait for "
                                                      "one to finish or cancel it",
                           {"limit": settings.export_max_active_per_user}, headers={"Retry-After": "10"})
        if jobs.total_result_bytes(conn) >= settings.export_max_total_bytes:
            raise ApiError(507, errors.STORAGE_FULL, "the export volume quota is used up; try again after older exports "
                                                     "expire")
        job = jobs.enqueue(conn, queue="export", kind="export", owner_id=principal.user_id, input=job_input,
                           dataset_id=row["dataset_id"], dataset_version=row["version"],
                           max_attempts=settings.job_max_attempts, progress_message="queued")
    log.info("export %s queued %s %s@v%s %s by %s", job["job_id"], req.kind, row["dataset_id"], row["version"], fmt,
             principal.username)
    return JSONResponse(status_code=202, content=views.export_job(job))


# Export job visible to the caller (owner or admin), else 404. Used by the job routes.
def _own_job(request: Request, principal: Principal, job_id: uuid.UUID) -> dict:
    with request.app.state.db.connection() as conn:
        row = jobs.get(conn, job_id)
    if row is None or row["queue"] != "export" or (row["owner_id"] != principal.user_id and not principal.is_admin):
        raise ApiError(404, errors.JOB_NOT_FOUND, f"export job {job_id} was not found")
    return row


# GET /api/exports: my export jobs (admins: all=true).
@router.get("", response_model=Page, responses=_E, summary="My export jobs (admins: all=true for everyone's)")
def list_exports(request: Request, principal: UserPrincipal, all: bool = False,
                 status: str | None = Query(None, pattern="^(queued|running|completed|failed|cancelled|expired)$"),
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    owner = None if (all and principal.is_admin) else principal.user_id
    with request.app.state.db.connection() as conn:
        rows, total = jobs.list_jobs(conn, queue="export", owner_id=owner, status=status, limit=limit, offset=offset)
    return {"items": [views.export_job(r) for r in rows], "total": total, "offset": offset, "limit": limit, "links": {}}


# GET /api/exports/{id}: status, progress and result.
@router.get("/{job_id}", response_model=ExportJob, responses=_E, summary="Export job status, progress and result")
def get_export(job_id: uuid.UUID, request: Request, principal: UserPrincipal):
    return views.export_job(_own_job(request, principal, job_id))


# POST /api/exports/{id}/cancel: cancel a queued/running export (409 when finished).
@router.post("/{job_id}/cancel", response_model=ExportJob, responses=_E, summary="Cancel a queued or running export")
def cancel_export(job_id: uuid.UUID, request: Request, principal: UserPrincipal):
    row = _own_job(request, principal, job_id)
    if row["status"] not in jobs.ACTIVE:
        raise ApiError(409, errors.JOB_FINISHED, f"export job {job_id} is already {row['status']}",
                       {"status": row["status"]})
    with request.app.state.db.connection() as conn:
        row = jobs.request_cancel(conn, job_id)
    log.info("export %s cancel requested by %s", job_id, principal.username)
    return views.export_job(row)


# A downloadable job (409/410 otherwise) whose dataset the caller may still read (403). Used by download/metadata.
def _completed(request: Request, principal: Principal, job_id: uuid.UUID) -> dict:
    row = _own_job(request, principal, job_id)
    if row["status"] == "failed":
        raise ApiError(409, errors.JOB_FAILED, f"export job {job_id} failed; nothing to download",
                       {"error": row.get("error")})
    if row["status"] == "cancelled":
        raise ApiError(409, errors.JOB_FAILED, f"export job {job_id} was cancelled; nothing to download")
    if views.is_expired(row):
        raise ApiError(410, errors.EXPORT_EXPIRED, f"export job {job_id} has expired; request the export again")
    if row["status"] != "completed":
        raise ApiError(409, errors.JOB_NOT_COMPLETED, f"export job {job_id} is {row['status']}; retry when completed",
                       {"status": row["status"], "progress": row.get("progress")}, headers={"Retry-After": "5"})
    settings = request.app.state.settings
    with request.app.state.db.connection() as conn:
        access = catalog.access_for(conn, settings, [row["dataset_id"]])[row["dataset_id"]]
    if not catalog.can_read(access, principal.role):
        raise ApiError(403, errors.FORBIDDEN, "this dataset is no longer available to your role")
    return row


# ETag, Repr-Digest and X-Checksum-SHA256 headers for a file. Used by download.
def _digest_headers(sha_hex: str | None) -> dict[str, str]:
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if sha_hex:
        headers["Repr-Digest"] = "sha-256=:" + base64.b64encode(bytes.fromhex(sha_hex)).decode("ascii") + ":"
        headers["X-Checksum-SHA256"] = sha_hex
        headers["ETag"] = f'"sha256-{sha_hex}"'
    return headers


_DOWNLOAD_RESPONSES = {
    **_E, 200: {"content": {"image/tiff": {}, "application/geo+json": {}}, "description": "The file (attachment). "
                "Headers: Content-Disposition, Content-Length, ETag, Repr-Digest (sha-256), X-Checksum-SHA256, "
                "Accept-Ranges."},
    206: {"description": "Partial content (HTTP Range)"}}


# HEAD /api/exports/{id}/download: headers only.
@router.head("/{job_id}/download", response_class=FileResponse, responses=_DOWNLOAD_RESPONSES,
             summary="Headers of a completed export (size, checksum) without the body")
def download_head(job_id: uuid.UUID, request: Request, principal: UserPrincipal):
    return download(job_id, request, principal)


# GET /api/exports/{id}/download: stream the file from disk (Range supported).
@router.get("/{job_id}/download", response_class=FileResponse, responses=_DOWNLOAD_RESPONSES,
            summary="Download a completed export (streams from disk; HTTP Range supported)")
def download(job_id: uuid.UUID, request: Request, principal: UserPrincipal):
    row = _completed(request, principal, job_id)
    settings = request.app.state.settings
    result = row.get("result") or {}
    try:
        if result.get("source") == "native_asset":
            with request.app.state.db.connection() as conn:
                cat = catalog.get_version(conn, row["dataset_id"], row["dataset_version"])
            if cat is None:
                raise ApiError(410, errors.EXPORT_EXPIRED, "the dataset version is no longer published")
            path = storage.resolve_asset(settings.asset_root, cat["asset_path"], cat["kind"])
        else:
            path = storage.resolve_result(settings.export_root, row["result_path"])
    except StorageError as exc:
        log.warning("download %s: %s", job_id, exc.code)
        raise ApiError(410, errors.EXPORT_EXPIRED, "the export file is no longer available; request it again") from None
    log.info("download %s by %s", job_id, principal.username)
    return FileResponse(path, media_type=result.get("media_type") or "application/octet-stream",
                        filename=result.get("filename") or path.name, headers=_digest_headers(row.get("result_sha256")))


# GET /api/exports/{id}/metadata: JSON sidecar (CRS, grid, NoData, units, provenance).
@router.get("/{job_id}/metadata", responses=_E, summary="Metadata sidecar: CRS, grid, NoData, units, provenance")
def metadata(job_id: uuid.UUID, request: Request, principal: UserPrincipal):
    row = _completed(request, principal, job_id)
    view = views.export_job(row)
    body = {"job_id": view["job_id"], "input": view["input"], "result": view["result"],
            "created_at": view["created_at"], "finished_at": view["finished_at"], "expires_at": view["expires_at"]}
    return JSONResponse(body, headers={"Content-Disposition":
                                       f'attachment; filename="{(view["result"] or {}).get("filename", "export")}.json"',
                                       "Cache-Control": "private, no-store"})
