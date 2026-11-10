"""``/api/catalog``: path-free dataset discovery filtered by the caller's role and each dataset's access level.

Anonymous callers see ``public`` datasets only (an empty list by default, since the default access level
is ``users``); signed-in users see ``public`` + ``users``; admins see everything. A dataset the caller may
not see answers 404, exactly like a dataset that does not exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from . import catalog, errors
from .auth import OptionalPrincipal
from .errors import ApiError
from .models import ErrorResponse, Page

router = APIRouter(prefix="/api/catalog", tags=["catalog"])
_E = {code: {"model": ErrorResponse} for code in (400, 401, 404, 422)}


def _parse_bbox(text: str | None) -> list[float] | None:
    if text is None:
        return None
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        raise ApiError(422, errors.INVALID_REQUEST, "bbox must be minLon,minLat,maxLon,maxLat (EPSG:4326)") from None
    if len(values) != 4 or not (-180 <= values[0] < values[2] <= 180 and -90 <= values[1] < values[3] <= 90):
        raise ApiError(422, errors.INVALID_REQUEST, "bbox must be minLon,minLat,maxLon,maxLat (EPSG:4326)")
    return values


@router.get("/datasets", response_model=Page, responses=_E, summary="Discover datasets (current ready versions)")
def list_datasets(request: Request, principal: OptionalPrincipal,
                  kind: str | None = Query(None, pattern="^(raster|vector)$"),
                  product_level: str | None = Query(None, pattern="^(source|level1|level2|other)$"),
                  year: int | None = Query(None, ge=1800, le=2200),
                  q: str | None = Query(None, max_length=100),
                  bbox: str | None = Query(None, max_length=200, description="minLon,minLat,maxLon,maxLat"),
                  offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    settings = request.app.state.settings
    role = principal.role if principal else None
    with request.app.state.db.connection() as conn:
        rows, total = catalog.list_current(conn, settings, role=role, kind=kind, product_level=product_level, year=year,
                                           q=q, bbox=_parse_bbox(bbox), limit=limit, offset=offset)
        versions = catalog.ready_versions(conn, [r["dataset_id"] for r in rows])
    items = []
    for r in rows:
        rec = catalog.public_record(r, access=r["access"])
        rec["versions"] = [{"version": v["version"], "status": v["status"]} for v in versions.get(r["dataset_id"], [])]
        items.append(rec)
    links = {}
    if offset + limit < total:
        links["next"] = f"/api/catalog/datasets?offset={offset + limit}&limit={limit}"
    return {"items": items, "total": total, "offset": offset, "limit": limit, "links": links}


def _visible(conn, settings, dataset_id: str, role: str | None) -> str:
    if not catalog.valid_dataset_id(dataset_id):
        raise ApiError(422, errors.INVALID_DATASET_ID, "dataset_id must be a lowercase catalog slug")
    access = catalog.access_for(conn, settings, [dataset_id])[dataset_id]
    if not catalog.can_read(access, role):
        raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' was not found")
    return access


@router.get("/datasets/{dataset_id}", responses=_E, summary="Current ready version of one dataset + its versions")
def get_dataset(dataset_id: str, request: Request, principal: OptionalPrincipal):
    settings = request.app.state.settings
    role = principal.role if principal else None
    with request.app.state.db.connection() as conn:
        access = _visible(conn, settings, dataset_id, role)
        row = catalog.get_version(conn, dataset_id, None)
        if row is None:
            raise ApiError(404, errors.DATASET_NOT_FOUND, f"dataset '{dataset_id}' has no ready version")
        versions = catalog.ready_versions(conn, [dataset_id]).get(dataset_id, [])
    rec = catalog.public_record(row, access=access)
    rec["versions"] = [{"version": v["version"], "status": v["status"], "created_at": v["created_at"]} for v in versions]
    rec["links"] = {"self": f"/api/catalog/datasets/{dataset_id}", "ogc": f"/ogc/collections/{dataset_id}",
                    "exports": "/api/exports"}
    return rec


@router.get("/datasets/{dataset_id}/versions/{version}", responses=_E, summary="One ready version")
def get_dataset_version(dataset_id: str, version: int, request: Request, principal: OptionalPrincipal):
    settings = request.app.state.settings
    role = principal.role if principal else None
    with request.app.state.db.connection() as conn:
        access = _visible(conn, settings, dataset_id, role)
        row = catalog.get_version(conn, dataset_id, version)
    if row is None:
        raise ApiError(404, errors.VERSION_NOT_FOUND, f"dataset '{dataset_id}' has no ready version {version}")
    return catalog.public_record(row, access=access)
