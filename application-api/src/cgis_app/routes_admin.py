# /admin routes (admin role only): upload -> validating -> validated -> publishing -> ready, failed
# (re-validate or replace), discard, access policy, job progress. Bytes go to server-named staging files; the
# ingest worker runs the existing importer. Same version + same checksum is idempotent (200); a ready version
# with other bytes is 409 CHECKSUM_CONFLICT.

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from . import catalog, errors, jobs, storage, upload_stream, views
from .auth import AdminPrincipal
from .errors import ApiError
from .models import AccessUpdate, AdminJob, ErrorResponse, Page, UploadMetadata

log = logging.getLogger("cgis_app.admin")
router = APIRouter(prefix="/admin", tags=["admin"])

_E = {code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 413, 415, 422, 507)}
LIVE = ("staged", "validating", "validated", "publishing", "failed")
VERSION_STATUSES = ("staged", "validating", "validated", "publishing", "ready", "failed")


# Current UTC time. Used by _manifest_entry.
def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ version/dataset views
# Non-discarded uploads (with uploader name). Used by _datasets.
def _uploads_for(conn: psycopg.Connection, dataset_ids: list[str] | None = None) -> list[dict]:
    where = "u.status <> 'discarded'"
    params: list[Any] = []
    if dataset_ids is not None:
        where += " AND u.dataset_id = ANY(%s)"
        params.append(dataset_ids)
    return conn.execute(
        f"SELECT u.*, us.username AS created_by_name FROM cgis_app.uploads u "
        f"LEFT JOIN cgis_app.users us ON us.user_id = u.created_by WHERE {where} "
        "ORDER BY u.dataset_id, u.version, u.created_at", params).fetchall()


# Catalog rows of all or some datasets. Used by _datasets.
def _catalog_rows(conn: psycopg.Connection, dataset_ids: list[str] | None = None) -> list[dict]:
    where, params = "", []
    if dataset_ids is not None:
        where = "WHERE d.dataset_id = ANY(%s)"
        params.append(dataset_ids)
    return conn.execute(
        "SELECT d.dataset_id, d.version, d.title, d.kind, d.product_level, d.year, d.units, d.crs, d.status, "
        "d.checksum_sha256, d.source, d.provenance, d.created_at, d.updated_at, d.resolution_x, d.resolution_y, "
        f"d.nodata FROM catalog.datasets d {where} ORDER BY d.dataset_id, d.version", params).fetchall()


# Validation view of a failed catalog import. Used by _version_record.
def _validation_from_catalog(row: dict) -> dict | None:
    if row["status"] != "failed":
        return None
    err = ((row.get("provenance") or {}).get("ingestion") or {}).get("error") or {}
    return {"ok": False, "errors": [{"code": str(err.get("code", "import_failed")).upper(),
                                     "message": str(err.get("message", "the import failed"))}], "warnings": []}


# Merge one version's catalog row and uploads into status, validation and allowed actions.
# Used by _datasets.
def _version_record(version: int, cat: dict | None, ups: list[dict]) -> dict[str, Any]:
    live = next((u for u in reversed(ups) if u["status"] in LIVE), None)
    latest = live or (ups[-1] if ups else None)
    if cat is not None and cat["status"] == "ready":
        status = "ready"
    elif live is not None:
        status = live["status"]
    elif cat is not None:
        status = cat["status"]
    else:
        status = "failed" if latest is not None and latest["status"] == "failed" else (latest or {}).get("status")
    meta = (latest or {}).get("metadata") or {}
    validation = (latest or {}).get("validation") if (live is not None or cat is None or cat["status"] != "ready") \
        else None
    if validation is None and cat is not None:
        validation = _validation_from_catalog(cat)
    record = {
        "version": version,
        "status": status,
        "catalog_status": cat["status"] if cat else None,
        "upload_status": latest["status"] if latest else None,
        "upload_id": str(latest["upload_id"]) if latest else None,
        "title": (cat or {}).get("title") or meta.get("title"),
        "kind": (cat or {}).get("kind") or (latest or {}).get("kind"),
        "checksum_sha256": (cat or {}).get("checksum_sha256") or (latest or {}).get("checksum_sha256"),
        "size_bytes": (latest or {}).get("size_bytes"),
        "crs": (cat or {}).get("crs") or (((latest or {}).get("validation") or {}).get("summary") or {}).get("crs"),
        "units": (cat or {}).get("units") or meta.get("units"),
        "year": (cat or {}).get("year") if cat else meta.get("year"),
        "source": (cat or {}).get("source") or meta.get("source"),
        "original_filename": (latest or {}).get("original_filename"),
        "created_by": (latest or {}).get("created_by_name"),
        "created_at": (cat or latest or {}).get("created_at"),
        "updated_at": max([r["updated_at"] for r in ([cat] if cat else []) + ups]),
        "published_at": (latest or {}).get("published_at"),
        "provenance": (cat or {}).get("provenance") if cat else meta.get("provenance"),
        "validation": validation,
        "error": (latest or {}).get("error") if status == "failed" else None,
        "actions": [],
    }
    if status in ("staged", "failed") and live is not None:
        record["actions"].append("validate")
    if status == "validated":
        record["actions"].append("publish")
    if live is not None and status in ("staged", "validated", "failed"):
        record["actions"].append("discard")
    return record


# Per-dataset summaries with every version record. Used by the dataset list/detail/version routes.
def _datasets(conn: psycopg.Connection, settings, dataset_ids: list[str] | None = None) -> dict[str, dict]:
    cats = _catalog_rows(conn, dataset_ids)
    ups = _uploads_for(conn, dataset_ids)
    by_id: dict[str, dict[int, dict]] = {}
    for c in cats:
        by_id.setdefault(c["dataset_id"], {}).setdefault(c["version"], {"cat": None, "ups": []})["cat"] = c
    for u in ups:
        by_id.setdefault(u["dataset_id"], {}).setdefault(u["version"], {"cat": None, "ups": []})["ups"].append(u)
    access = catalog.access_for(conn, settings, list(by_id))
    out = {}
    for did, versions in by_id.items():
        records = [_version_record(v, parts["cat"], parts["ups"]) for v, parts in sorted(versions.items(),
                                                                                         reverse=True)]
        records = [r for r in records if r["status"] is not None]
        if not records:
            continue
        ready = [r["version"] for r in records if r["status"] == "ready"]
        head = records[0]
        newest_ready = next((versions[v]["cat"] for v in sorted(versions, reverse=True)
                             if versions[v]["cat"] and versions[v]["cat"]["status"] == "ready"), None)
        ref = newest_ready or versions[head["version"]]["cat"] or {}
        meta = next((u["metadata"] for v in sorted(versions, reverse=True) for u in versions[v]["ups"]), {})
        out[did] = {
            "dataset_id": did,
            "title": ref.get("title") or meta.get("title"),
            "kind": ref.get("kind") or head["kind"],
            "product_level": ref.get("product_level") or meta.get("product_level"),
            "year": ref.get("year") if ref else meta.get("year"),
            "units": ref.get("units") or meta.get("units"),
            "current_version": max(ready) if ready else None,
            "latest_version": head["version"],
            "latest_status": head["status"],
            "versions_count": len(records),
            "updated_at": max(r["updated_at"] for r in records),
            "access": access[did],
            "versions": records,
        }
    return out


# Dataset summary without its versions. Used by list_datasets.
def _summary(ds: dict) -> dict:
    return {k: v for k, v in ds.items() if k != "versions"}


# GET /admin/datasets: every dataset with version status (filters q/status/kind, paged).
@router.get("/datasets", response_model=Page, responses=_E,
            summary="List every dataset with version status (catalog + staged uploads)")
def list_datasets(request: Request, principal: AdminPrincipal,
                  q: str | None = Query(None, max_length=100), status: str | None = Query(None),
                  kind: str | None = Query(None, pattern="^(raster|vector)$"),
                  offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    if status is not None and status not in VERSION_STATUSES:
        raise ApiError(422, errors.INVALID_REQUEST, f"status must be one of {', '.join(VERSION_STATUSES)}")
    with request.app.state.db.connection() as conn:
        items = list(_datasets(conn, request.app.state.settings).values())
    if q:
        needle = q.lower()
        items = [d for d in items if needle in d["dataset_id"] or needle in (d["title"] or "").lower()]
    if status:
        items = [d for d in items if any(v["status"] == status for v in d["versions"])]
    if kind:
        items = [d for d in items if d["kind"] == kind]
    items.sort(key=lambda d: d["dataset_id"])
    page = items[offset:offset + limit]
    links = {}
    if offset + limit < len(items):
        links["next"] = f"/admin/datasets?offset={offset + limit}&limit={limit}"
    if offset > 0:
        links["prev"] = f"/admin/datasets?offset={max(0, offset - limit)}&limit={limit}"
    return {"items": [_summary(d) for d in page], "total": len(items), "offset": offset, "limit": limit,
            "links": links}


# 422 INVALID_DATASET_ID unless a catalog slug. Used by the per-dataset routes.
def _check_id(dataset_id: str) -> None:
    if not catalog.valid_dataset_id(dataset_id):
        raise ApiError(422, errors.INVALID_DATASET_ID, "dataset_id must be a lowercase catalog slug")


# GET /admin/datasets/{id}: all versions plus recent ingest jobs.
@router.get("/datasets/{dataset_id}", responses=_E, summary="One dataset with every version, validation and "
                                                             "provenance")
def get_dataset(dataset_id: str, request: Request, principal: AdminPrincipal):
    _check_id(dataset_id)
    with request.app.state.db.connection() as conn:
        found = _datasets(conn, request.app.state.settings, [dataset_id]).get(dataset_id)
        if found:
            jobs_rows, _ = jobs.list_jobs(conn, queue="ingest", limit=20)
    if not found:
        raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' has no versions")
    found["recent_jobs"] = [views.admin_job(j) for j in jobs_rows if j["dataset_id"] == dataset_id]
    return found


# GET /admin/datasets/{id}/versions/{v}: one version record.
@router.get("/datasets/{dataset_id}/versions/{version}", responses=_E, summary="One version")
def get_version(dataset_id: str, version: int, request: Request, principal: AdminPrincipal):
    _check_id(dataset_id)
    with request.app.state.db.connection() as conn:
        found = _datasets(conn, request.app.state.settings, [dataset_id]).get(dataset_id)
    rec = next((v for v in (found or {}).get("versions", []) if v["version"] == version), None)
    if rec is None:
        raise ApiError(404, errors.VERSION_NOT_FOUND, f"dataset '{dataset_id}' has no version {version}")
    return rec


# ------------------------------------------------------------------ upload
_UPLOAD_DOC = """Multipart upload (admin only). Parts: ``metadata`` (JSON text of UploadMetadata) and ``file``
(GeoTIFF/COG ``.tif``/``.tiff`` or GeoJSON ``.geojson``/``.json``). The file is streamed to a server-named
staging file (limits: rasters ``APP_MAX_UPLOAD_BYTES``, GeoJSON ``APP_MAX_VECTOR_UPLOAD_BYTES``) and then
validated asynchronously by the importer.

* 202: new upload; poll ``links.self`` (``kind=upload``) until ``completed`` (validated) or ``failed``.
* 200: identical bytes already uploaded or ready for this version (idempotent; no new job).
* 409 ``CHECKSUM_CONFLICT``: the version is ready with different bytes - use a new version.
* 409 ``VERSION_EXISTS``: a different file for this version is staged/validating/validated - discard it first.
"""


# Parse the metadata part into UploadMetadata (no file/path/URL keys). Used by _register_upload.
def _parse_metadata(raw: bytes) -> UploadMetadata:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(422, errors.INVALID_METADATA, "metadata must be a JSON object (UTF-8 text)") from None
    if not isinstance(data, dict):
        raise ApiError(422, errors.INVALID_METADATA, "metadata must be a JSON object")
    if "file" in data or "asset_path" in data or "url" in data:
        raise ApiError(422, errors.INVALID_METADATA, "metadata must not name a file, path or URL; send the bytes "
                                                     "as the 'file' part")
    try:
        return UploadMetadata.model_validate(data)
    except ValidationError as exc:
        problems = [{"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "")} for e in exc.errors()][:20]
        raise ApiError(422, errors.INVALID_METADATA, "metadata is invalid", {"problems": problems}) from None


# Importer manifest entry for an upload (+upload provenance, default source). Used by _register_upload.
def _manifest_entry(meta: UploadMetadata, received: upload_stream.ReceivedUpload, principal, version: int,
                    upload_id: uuid.UUID) -> tuple[dict, list[str]]:
    warnings = []
    entry = meta.model_dump(exclude_none=True)
    entry["version"] = version
    entry.pop("checksum_sha256", None)
    if not meta.source:
        entry["source"] = f"admin upload via the CyberGIS application API by {principal.username}"
        warnings.append("no 'source' was given; recorded as an admin upload. Describe the real origin of the data "
                        "(product, release, producer) in 'source' for proper provenance.")
    provenance = dict(entry.get("provenance") or {})
    provenance["upload"] = {"upload_id": str(upload_id), "uploaded_by": principal.username,
                            "uploaded_at": _now().isoformat(), "original_filename": received.original_filename,
                            "size_bytes": received.size, "sha256": received.sha256, "via": "cgis-application-api"}
    entry["provenance"] = provenance
    entry["checksum_sha256"] = received.sha256  # the importer re-checks the staged bytes against it
    return entry, warnings


# 200 body for an unchanged/already uploaded repeat. Used by _register_upload.
def _idempotent_response(job_row: dict, outcome: str, upload_id=None) -> JSONResponse:
    body = views.admin_job(job_row)
    body["result"] = {**(body.get("result") or {}), "outcome": outcome}
    if upload_id:
        body["upload_id"] = str(upload_id)
    return JSONResponse(status_code=200, content=body)


# POST /admin/datasets/uploads: streamed multipart upload, validation queued.
@router.post("/datasets/uploads", status_code=202, response_model=AdminJob, summary="Upload a dataset version",
             description=_UPLOAD_DOC, responses={**_E, 200: {"model": AdminJob, "description": "Idempotent repeat"}},
             openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
                 "type": "object", "required": ["metadata", "file"], "properties": {
                     "metadata": {"type": "string", "description": "JSON text of UploadMetadata"},
                     "file": {"type": "string", "format": "binary"}}}}}}})
async def upload(request: Request, principal: AdminPrincipal):
    return await _handle_upload(request, principal)


# POST /admin/datasets: alias of the upload route.
@router.post("/datasets", status_code=202, response_model=AdminJob, include_in_schema=True,
             summary="Upload a dataset version (alias of POST /admin/datasets/uploads)", description=_UPLOAD_DOC,
             responses={**_E, 200: {"model": AdminJob}},
             openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
                 "type": "object", "required": ["metadata", "file"], "properties": {
                     "metadata": {"type": "string"}, "file": {"type": "string", "format": "binary"}}}}}}})
async def upload_alias(request: Request, principal: AdminPrincipal):
    return await _handle_upload(request, principal)


# Shared upload flow: encoding/preflight checks, stream to staging, register (staged file removed on
# error). Used by upload and upload_alias.
async def _handle_upload(request: Request, principal) -> Any:
    import anyio

    settings = request.app.state.settings
    if request.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        raise ApiError(415, errors.UNSUPPORTED_MEDIA_TYPE, "compressed request bodies are not accepted")
    length = request.headers.get("content-length")
    content_length = int(length) if length and length.isdigit() else None
    root = request.app.state.staging_root
    await anyio.to_thread.run_sync(upload_stream.preflight, settings, root, content_length)
    upload_id = uuid.uuid4()
    received = await upload_stream.receive_multipart(request.stream(), request.headers.get("content-type", ""),
                                                     settings=settings, staging_root=root,
                                                     staged_stem=upload_id.hex)
    try:
        return await anyio.to_thread.run_sync(_register_upload, request, principal, received, upload_id)
    except BaseException:
        storage.remove_staged(root, received.staged_name)
        raise


# Check metadata vs file, resolve version/kind/idempotency/conflicts under an advisory lock, record
# the upload and enqueue validation. Used by _handle_upload (in a thread).
def _register_upload(request: Request, principal, received: upload_stream.ReceivedUpload, upload_id: uuid.UUID):
    settings = request.app.state.settings
    root = request.app.state.staging_root
    meta = _parse_metadata(received.metadata_raw)
    if meta.kind != received.sniffed_kind:
        raise ApiError(422, errors.INVALID_METADATA,
                       f"metadata says kind={meta.kind} but the file is a "
                       f"{'GeoTIFF' if received.sniffed_kind == 'raster' else 'GeoJSON'} ({received.sniffed_kind})")
    if meta.kind == "raster" and not meta.units:
        raise ApiError(422, errors.INVALID_METADATA, "'units' is required for rasters (e.g. people_per_cell); "
                                                     "counts and densities are never converted")
    if meta.checksum_sha256 and meta.checksum_sha256.lower() != received.sha256:
        raise ApiError(422, errors.CHECKSUM_MISMATCH, "the received file does not match metadata.checksum_sha256",
                       {"expected": meta.checksum_sha256.lower(), "received": received.sha256})
    db = request.app.state.db
    with db.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"cgis_app.upload:{meta.dataset_id}",))
        kinds = {r["kind"] for r in conn.execute(
            "SELECT kind FROM catalog.datasets WHERE dataset_id = %s UNION SELECT kind FROM cgis_app.uploads "
            "WHERE dataset_id = %s AND status <> 'discarded'", (meta.dataset_id, meta.dataset_id)).fetchall()}
        if kinds and kinds != {meta.kind}:
            raise ApiError(409, errors.KIND_CONFLICT, f"dataset '{meta.dataset_id}' is a {', '.join(sorted(kinds))} "
                                                      f"dataset; a new version must have the same kind")
        version = meta.version
        if version is None:
            row = conn.execute(
                "SELECT greatest((SELECT max(version) FROM catalog.datasets WHERE dataset_id = %s), "
                "(SELECT max(version) FROM cgis_app.uploads WHERE dataset_id = %s AND status <> 'discarded')) AS v",
                (meta.dataset_id, meta.dataset_id)).fetchone()
            version = (row["v"] or 0) + 1
        cat = conn.execute("SELECT status, checksum_sha256 FROM catalog.datasets WHERE dataset_id = %s AND "
                           "version = %s", (meta.dataset_id, version)).fetchone()
        if cat is not None and cat["status"] == "ready":
            if cat["checksum_sha256"] == received.sha256:
                job = jobs.insert_completed(conn, queue="ingest", kind="upload", owner_id=principal.user_id,
                                            input={"dataset_id": meta.dataset_id, "version": version},
                                            result={"outcome": "unchanged", "message": "this exact file is already "
                                                    "published as this version; nothing to do"},
                                            dataset_id=meta.dataset_id, dataset_version=version)
                log.info("upload %s@v%s unchanged (already ready, same checksum)", meta.dataset_id, version)
                storage.remove_staged(root, received.staged_name)
                return _idempotent_response(job, "unchanged")
            nxt = conn.execute("SELECT max(version) + 1 AS v FROM catalog.datasets WHERE dataset_id = %s",
                               (meta.dataset_id,)).fetchone()["v"]
            raise ApiError(409, errors.CHECKSUM_CONFLICT,
                           f"{meta.dataset_id} v{version} is already published with different content; ready versions "
                           "are immutable - upload it as a new version",
                           {"existing_checksum": cat["checksum_sha256"], "new_checksum": received.sha256,
                            "suggested_version": nxt})
        live = conn.execute("SELECT * FROM cgis_app.uploads WHERE dataset_id = %s AND version = %s AND status = ANY(%s) "
                            "FOR UPDATE", (meta.dataset_id, version, list(LIVE))).fetchone()
        if live is not None:
            if live["checksum_sha256"] == received.sha256 and live["status"] != "failed":
                last = conn.execute("SELECT * FROM cgis_app.jobs WHERE upload_id = %s ORDER BY created_at DESC LIMIT 1",
                                    (live["upload_id"],)).fetchone()
                storage.remove_staged(root, received.staged_name)
                if last is None:
                    last = jobs.insert_completed(conn, queue="ingest", kind="upload", owner_id=principal.user_id,
                                                 input={"dataset_id": meta.dataset_id, "version": version},
                                                 result={"outcome": "already_uploaded"}, upload_id=live["upload_id"],
                                                 dataset_id=meta.dataset_id, dataset_version=version)
                return _idempotent_response(last, "already_uploaded", live["upload_id"])
            if live["status"] != "failed":
                raise ApiError(409, errors.VERSION_EXISTS,
                               f"{meta.dataset_id} v{version} already has a different upload in state "
                               f"'{live['status']}'; discard it (DELETE /admin/datasets/{meta.dataset_id}/versions/"
                               f"{version}) or use another version",
                               {"upload_id": str(live["upload_id"]), "status": live["status"]})
            # A failed upload of the same version is superseded by the new file.
            conn.execute("UPDATE cgis_app.uploads SET status = 'discarded', result = %s WHERE upload_id = %s",
                         (Jsonb({"superseded_by": str(upload_id)}), live["upload_id"]))
            storage.remove_staged(root, live["staged_name"])
        entry, warnings = _manifest_entry(meta, received, principal, version, upload_id)
        conn.execute(
            "INSERT INTO cgis_app.uploads (upload_id, dataset_id, version, kind, metadata, original_filename, "
            "staged_name, size_bytes, checksum_sha256, status, validation, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'validating', %s, %s)",
            (upload_id, meta.dataset_id, version, meta.kind, Jsonb(entry), received.original_filename,
             received.staged_name, received.size, received.sha256,
             Jsonb({"ok": None, "errors": [], "warnings": warnings}), principal.user_id))
        job = jobs.enqueue(conn, queue="ingest", kind="upload", owner_id=principal.user_id,
                           input={"dataset_id": meta.dataset_id, "version": version, "upload_id": str(upload_id),
                                  "size_bytes": received.size, "sha256": received.sha256},
                           upload_id=upload_id, dataset_id=meta.dataset_id, dataset_version=version,
                           max_attempts=settings.job_max_attempts, progress_message="queued for validation")
    log.info("upload %s@v%s accepted (%d bytes, sha256 %s…) by %s", meta.dataset_id, version, received.size,
             received.sha256[:12], principal.username)
    body = views.admin_job(job)
    body["result"] = {"outcome": "uploaded", "size_bytes": received.size, "checksum_sha256": received.sha256,
                      "warnings": warnings}
    return JSONResponse(status_code=202, content=body)


# ------------------------------------------------------------------ validate / publish / discard
# The live upload of a version (locked), or 409 READY_IMMUTABLE / 404. Used by validate/publish/discard.
def _live_upload(conn, dataset_id: str, version: int) -> dict:
    live = conn.execute("SELECT * FROM cgis_app.uploads WHERE dataset_id = %s AND version = %s AND status = ANY(%s) "
                        "FOR UPDATE", (dataset_id, version, list(LIVE))).fetchone()
    if live is not None:
        return live
    cat = conn.execute("SELECT status FROM catalog.datasets WHERE dataset_id = %s AND version = %s",
                       (dataset_id, version)).fetchone()
    if cat is not None and cat["status"] == "ready":
        raise ApiError(409, errors.READY_IMMUTABLE, f"{dataset_id} v{version} is ready; ready versions are immutable",
                       {"status": "ready"})
    raise ApiError(404, errors.VERSION_NOT_FOUND, f"no staged upload for {dataset_id} v{version}")


# POST .../versions/{v}/validate: re-validate a staged/failed/validated upload.
@router.post("/datasets/{dataset_id}/versions/{version}/validate", status_code=202, response_model=AdminJob,
             responses=_E, summary="(Re)validate a staged or failed upload with the importer")
def validate(dataset_id: str, version: int, request: Request, principal: AdminPrincipal):
    _check_id(dataset_id)
    settings = request.app.state.settings
    with request.app.state.db.connection() as conn, conn.transaction():
        live = _live_upload(conn, dataset_id, version)
        if live["status"] not in ("staged", "failed", "validated"):
            raise ApiError(409, errors.INVALID_STATE, f"cannot validate while the upload is '{live['status']}'",
                           {"status": live["status"]})
        conn.execute("UPDATE cgis_app.uploads SET status = 'validating', error = NULL WHERE upload_id = %s",
                     (live["upload_id"],))
        job = jobs.enqueue(conn, queue="ingest", kind="validate", owner_id=principal.user_id,
                           input={"dataset_id": dataset_id, "version": version, "upload_id": str(live["upload_id"])},
                           upload_id=live["upload_id"], dataset_id=dataset_id, dataset_version=version,
                           max_attempts=settings.job_max_attempts, progress_message="queued for validation")
    return JSONResponse(status_code=202, content=views.admin_job(job))


# POST .../versions/{v}/publish: queue publication of a validated upload.
@router.post("/datasets/{dataset_id}/versions/{version}/publish", status_code=202, response_model=AdminJob,
             responses=_E, summary="Publish a validated upload (importer: COG/normalize, catalog ready)")
def publish(dataset_id: str, version: int, request: Request, principal: AdminPrincipal):
    _check_id(dataset_id)
    settings = request.app.state.settings
    with request.app.state.db.connection() as conn, conn.transaction():
        live = _live_upload(conn, dataset_id, version)
        if live["status"] != "validated":
            raise ApiError(409, errors.INVALID_STATE, f"only a validated upload can be published (current state: "
                                                      f"'{live['status']}')", {"status": live["status"]})
        conn.execute("UPDATE cgis_app.uploads SET status = 'publishing' WHERE upload_id = %s", (live["upload_id"],))
        job = jobs.enqueue(conn, queue="ingest", kind="publish", owner_id=principal.user_id,
                           input={"dataset_id": dataset_id, "version": version, "upload_id": str(live["upload_id"])},
                           upload_id=live["upload_id"], dataset_id=dataset_id, dataset_version=version,
                           max_attempts=settings.job_max_attempts, progress_message="queued for publication")
    log.info("publish %s@v%s requested by %s", dataset_id, version, principal.username)
    return JSONResponse(status_code=202, content=views.admin_job(job))


# DELETE .../versions/{v}: discard an unpublished upload and its staged file.
@router.delete("/datasets/{dataset_id}/versions/{version}", status_code=204, responses=_E,
               summary="Discard an unpublished upload (ready versions are immutable)")
def discard(dataset_id: str, version: int, request: Request, principal: AdminPrincipal):
    _check_id(dataset_id)
    with request.app.state.db.connection() as conn, conn.transaction():
        live = _live_upload(conn, dataset_id, version)
        if live["status"] not in ("staged", "validated", "failed"):
            raise ApiError(409, errors.INVALID_STATE, f"cannot discard while the upload is '{live['status']}'",
                           {"status": live["status"]})
        conn.execute("UPDATE cgis_app.uploads SET status = 'discarded' WHERE upload_id = %s", (live["upload_id"],))
    storage.remove_staged(request.app.state.staging_root, live["staged_name"])
    log.info("upload %s@v%s discarded by %s", dataset_id, version, principal.username)
    return Response(status_code=204)


# ------------------------------------------------------------------ access policy
# PUT /admin/datasets/{id}/access: set public | users | admins.
@router.put("/datasets/{dataset_id}/access", responses=_E,
            summary="Set who may discover/export a dataset: public | users | admins")
def put_access(dataset_id: str, req: AccessUpdate, request: Request, principal: AdminPrincipal):
    _check_id(dataset_id)
    with request.app.state.db.connection() as conn:
        exists = catalog.dataset_exists(conn, dataset_id) or conn.execute(
            "SELECT 1 FROM cgis_app.uploads WHERE dataset_id = %s AND status <> 'discarded' LIMIT 1",
            (dataset_id,)).fetchone()
        if not exists:
            raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' was not found")
        row = catalog.set_access(conn, dataset_id, req.access, req.note, principal.user_id)
    log.info("access of %s set to %s by %s", dataset_id, req.access, principal.username)
    return {"dataset_id": dataset_id, "access": row["access"], "note": row["note"], "updated_at": row["updated_at"]}


# ------------------------------------------------------------------ jobs
# GET /admin/jobs: ingest jobs (filters status/kind, paged).
@router.get("/jobs", response_model=Page, responses=_E, summary="Admin jobs (upload/validate/publish)")
def list_admin_jobs(request: Request, principal: AdminPrincipal,
                    status: str | None = Query(None, pattern="^(queued|running|completed|failed|cancelled)$"),
                    kind: str | None = Query(None, pattern="^(upload|validate|publish)$"),
                    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    with request.app.state.db.connection() as conn:
        rows, total = jobs.list_jobs(conn, queue="ingest", status=status, kind=kind, limit=limit, offset=offset)
    return {"items": [views.admin_job(r) for r in rows], "total": total, "offset": offset, "limit": limit,
            "links": {}}


# GET /admin/jobs/{id}: one ingest job with progress.
@router.get("/jobs/{job_id}", response_model=AdminJob, responses=_E, summary="One admin job with progress")
def get_admin_job(job_id: uuid.UUID, request: Request, principal: AdminPrincipal):
    with request.app.state.db.connection() as conn:
        row = jobs.get(conn, job_id)
    if row is None or row["queue"] != "ingest":
        raise ApiError(404, errors.JOB_NOT_FOUND, f"admin job {job_id} was not found")
    return views.admin_job(row)
