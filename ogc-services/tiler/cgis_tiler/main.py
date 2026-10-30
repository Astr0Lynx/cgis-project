"""Restricted TiTiler application for catalog-registered COG assets.

VENDOR-SPECIFIC API. These routes are TiTiler's own interface (TileJSON, /info,
/point, ...). They are NOT an OGC API: there is no landing page, conformance
declaration or collections resource here, and nothing below is claimed as OGC
API - Tiles conformance. The standards-based interface is the OGC API - Tiles
publication in pygeoapi under ``/ogc/collections/{id}/tiles``.

Unlike the stock ``titiler.application`` there is no ``url=`` parameter and no
``/cog`` / ``/stac`` / ``/mosaic`` endpoints: a dataset can only be addressed by
its catalog ``dataset_id``, resolved server-side by :mod:`cgis_tiler.assets`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from morecantile import TileMatrixSets
from morecantile import tms as default_tms
from starlette.exceptions import HTTPException as StarletteHTTPException
from titiler.core.dependencies import BidxParams
from titiler.core.errors import DEFAULT_STATUS_CODES
from titiler.core.factory import TilerFactory, TMSFactory

from .assets import AssetError, AssetResolver
from .catalog import Catalog, conninfo_from_env

LOGGER = logging.getLogger("cgis_tiler")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

ASSET_ROOT = FsPath(os.environ.get("CGIS_ASSET_ROOT", "/data/assets"))
CACHE_TTL = float(os.environ.get("CGIS_CATALOG_CACHE_TTL", "30"))
SUPPORTED_TMS_IDS = ("WebMercatorQuad", "WorldCRS84Quad")

catalog = Catalog(conninfo_from_env())
resolver = AssetResolver(ASSET_ROOT, catalog.lookup_raster, ttl_seconds=CACHE_TTL)
supported_tms = TileMatrixSets({tms_id: default_tms.get(tms_id) for tms_id in SUPPORTED_TMS_IDS})


def dataset_path(
    dataset_id: Annotated[str, Path(description="Catalog dataset_id of a ready raster dataset")],
) -> str:
    """titiler path dependency: catalog dataset_id -> confined local file path."""
    _, path = resolver.resolve(dataset_id)
    return str(path)


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


app = FastAPI(
    title="CyberGIS raster tiles (TiTiler, vendor-specific)",
    description=(
        "Vendor-specific TiTiler routes for catalog-registered raster assets. "
        "Not an OGC API. The OGC API - Tiles interface is served by the `ogc` service."
    ),
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
)

cog = TilerFactory(
    router_prefix="/datasets/{dataset_id}",
    path_dependency=dataset_path,
    layer_dependency=BidxParams,  # band selection only; no band-math expressions
    supported_tms=supported_tms,
    add_preview=False,
    add_part=False,
    add_viewer=False,
)
app.include_router(cog.router, prefix="/datasets/{dataset_id}", tags=["Datasets (vendor-specific)"])

tms_factory = TMSFactory(supported_tms=supported_tms)
app.include_router(tms_factory.router, tags=["Tiling schemes (vendor-specific)"])


def _dataset_summary(request: Request, record) -> dict:
    base = str(request.base_url).rstrip("/") + f"/datasets/{record.dataset_id}"
    return {
        "dataset_id": record.dataset_id,
        "version": record.version,
        "title": record.title,
        "year": record.year,
        "crs": record.crs,
        "units": record.units,
        "nodata": record.nodata,
        "bbox": list(record.bbox) if record.bbox else None,
        "links": [
            {"rel": "tilejson", "type": "application/json",
             "href": f"{base}/WebMercatorQuad/tilejson.json"},
            {"rel": "tiles", "type": "image/png", "templated": True,
             "href": f"{base}/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png"},
            {"rel": "info", "type": "application/json", "href": f"{base}/info"},
        ],
    }


@app.get("/datasets", tags=["Datasets (vendor-specific)"], summary="List servable raster datasets")
def list_datasets(request: Request):
    return {
        "note": "Vendor-specific TiTiler discovery; not an OGC API resource.",
        "datasets": [_dataset_summary(request, r) for r in catalog.list_rasters()],
    }


@app.get("/datasets/{dataset_id}", tags=["Datasets (vendor-specific)"], summary="Describe one raster dataset")
def describe_dataset(request: Request, dataset_id: str):
    record, _ = resolver.resolve(dataset_id)
    return _dataset_summary(request, record)


@app.get("/healthz", tags=["Health"], summary="Liveness")
def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["Health"], summary="Readiness: catalog reachable and asset root mounted")
def readyz():
    if not ASSET_ROOT.is_dir():
        return JSONResponse(error_body("asset_root_missing", "Asset volume is not mounted"), status_code=503)
    catalog.ping()
    return {"status": "ready"}


@app.exception_handler(AssetError)
def _asset_error(request: Request, exc: AssetError):
    return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
    return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
def _validation_error(request: Request, exc: RequestValidationError):
    fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
    return JSONResponse(
        error_body("invalid_request", f"Invalid request parameters: {', '.join(fields)}"),
        status_code=422,
    )


def _register_rio_tiler_handlers() -> None:
    for exc_class, status_code in DEFAULT_STATUS_CODES.items():
        if exc_class is Exception:
            continue
        code = exc_class.__name__

        def handler(request: Request, exc: Exception, status_code=status_code, code=code):
            if status_code >= 500:
                LOGGER.exception("tile service error", exc_info=exc)
                return JSONResponse(error_body("internal_error", "Internal tile service error"), status_code=status_code)
            return JSONResponse(error_body(code, str(exc)), status_code=status_code)

        app.add_exception_handler(exc_class, handler)


_register_rio_tiler_handlers()


@app.exception_handler(Exception)
def _unhandled(request: Request, exc: Exception):
    LOGGER.exception("unhandled error", exc_info=exc)
    return JSONResponse(error_body("internal_error", "Internal tile service error"), status_code=500)
