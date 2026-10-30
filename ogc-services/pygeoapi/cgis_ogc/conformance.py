"""Standards adapter: align pygeoapi 0.24.0's advertised conformance with this deployment.

pygeoapi declares conformance classes per *provider type*, not per capability
actually enabled. For the resources the CyberGIS publisher generates, that
over-claims. This module corrects the declaration and the tiles OpenAPI, and
refuses to start if the configuration falls outside the invariants that make
the corrections true.

Changes (each covered by tests/integration/test_conformance.py):

* drop ``ogcapi-tiles-1 .../conf/mvt``: only PNG map tiles are served; add
  ``.../conf/png`` (OGC 20-057 /req/png/content).
* drop ``ogcapi-features-4 .../conf/create-replace-delete``: Part 4 is an OGC
  draft and no resource is ``editable``.
* drop ``ogcapi-common-3 .../conf/schemas`` and ``.../advanced-property-roles``:
  draft documents; not verified here.
* drop ``ogcapi-common-2 .../conf/collections``: Common Part 2 is a draft.
* OpenAPI: add the tileset resource ``/collections/{id}/tiles/{tileMatrixSetId}``
  (operationId suffix ``.collection.map.getTileSet``), which pygeoapi serves but
  omits, as required by /req/oas30/completeness.
* OpenAPI: declare ``datetime`` on every items path (/req/core/fc-time-definition);
  pygeoapi omits it when the provider has no ``time_field``. The ``sortby``
  parameter, which pygeoapi references from a GitHub ``master`` branch, is inlined.
* tileset metadata errors raised by a provider (e.g. unknown TileMatrixSet) are
  returned as OGC exception responses instead of HTTP 500.
* tile requests: HTTP content negotiation (``Accept``) selects PNG when no
  ``f`` is given (OGC 20-057 7.1.1), unacceptable formats get 406/400, and error
  responses are labelled ``application/json`` instead of ``image/png``.
* feature items/item responses in JSON are labelled ``application/geo+json``
  (OGC 17-069r4 /req/geojson/definition); pygeoapi sends ``application/json``.
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SUPPORTED_PYGEOAPI = "0.24.0"
TILE_PROVIDER = "cgis_ogc.pygeoapi_plugins.titiler_tiles.TiTilerTileProvider"
FEATURE_PROVIDER = "cgis_ogc.pygeoapi_plugins.postgresql.CatalogPostgreSQLProvider"

TILES_PNG = "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/png"
TILES_MVT = "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/mvt"
WITHDRAWN = frozenset({
    TILES_MVT,
    "http://www.opengis.net/spec/ogcapi-features-4/1.0/conf/create-replace-delete",
    "http://www.opengis.net/spec/ogcapi-common-3/1.0/conf/schemas",
    "http://www.opengis.net/spec/ogcapi-common-3/1.0/conf/advanced-property-roles",
    # OGC API - Common - Part 2 is a draft (1.0.0rc2); /collections is declared
    # through OGC API - Features - Part 1 instead (allowed by OGC 20-057 clause 11.2).
    "http://www.opengis.net/spec/ogcapi-common-2/1.0/conf/collections",
})

_applied = False


class UnsupportedConfiguration(RuntimeError):
    pass


def validate_config(config: dict) -> None:
    """Invariants under which the conformance corrections are accurate."""
    for name, resource in (config.get("resources") or {}).items():
        if resource.get("type") != "collection":
            raise UnsupportedConfiguration(f"{name}: only collections are published (got {resource.get('type')!r})")
        for provider in resource.get("providers", []):
            ptype, pname = provider.get("type"), provider.get("name")
            if provider.get("editable"):
                raise UnsupportedConfiguration(f"{name}: editable providers are not allowed")
            if ptype == "tile":
                if pname != TILE_PROVIDER or (provider.get("format") or {}).get("mimetype") != "image/png":
                    raise UnsupportedConfiguration(f"{name}: tile provider must be the PNG {TILE_PROVIDER}")
            elif ptype == "feature":
                if pname != FEATURE_PROVIDER:
                    raise UnsupportedConfiguration(f"{name}: feature provider must be {FEATURE_PROVIDER}")
            else:
                raise UnsupportedConfiguration(f"{name}: provider type {ptype!r} is not vetted")


def _correct_lists() -> None:
    import pygeoapi.api as core_api
    from pygeoapi.api import itemtypes, tiles

    for module, attr in ((core_api, "CONFORMANCE_CLASSES"), (tiles, "CONFORMANCE_CLASSES"),
                         (itemtypes, "CONFORMANCE_CLASSES_FEATURES")):
        current = getattr(module, attr)
        current[:] = [c for c in current if c not in WITHDRAWN]
    if TILES_PNG not in tiles.CONFORMANCE_CLASSES:
        tiles.CONFORMANCE_CLASSES.append(TILES_PNG)


def _patch_tiles_openapi() -> None:
    from pygeoapi.api import tiles
    from pygeoapi.openapi import OPENAPI_YAML

    original = tiles.get_oas_30

    @functools.wraps(original)
    def get_oas_30(cfg: dict, locale: str):
        tags, fragment = original(cfg, locale)
        paths = fragment["paths"]
        for path in [p for p in paths if p.startswith("/collections/") and p.endswith("/tiles")]:
            collection = path.split("/")[2]
            paths[f"{path}/{{tileMatrixSetId}}"] = {"get": {
                "summary": f"Fetch the {collection} map tileset metadata for one TileMatrixSet",
                "tags": [collection],
                "operationId": f"describe{collection.capitalize()}.collection.map.getTileSet",
                "parameters": [
                    {"$ref": f"{OPENAPI_YAML['oapit']}#/components/parameters/tileMatrixSetId"},
                    {"$ref": "#/components/parameters/f"},
                ],
                "responses": {
                    "200": {"$ref": f"{OPENAPI_YAML['oapit']}#/components/responses/TileSet"},
                    "404": {"$ref": f"{OPENAPI_YAML['oapif-1']}#/components/responses/NotFound"},
                    "500": {"$ref": f"{OPENAPI_YAML['oapif-1']}#/components/responses/ServerError"},
                },
            }}
        return tags, fragment

    tiles.get_oas_30 = get_oas_30


def _patch_items_openapi() -> None:
    from pygeoapi.api import itemtypes

    original = itemtypes.get_oas_30
    # Inline copy of the OGC 17-069r4 Requirement 25 fragment (clients such as
    # the CITE ETS do not resolve the remote $ref pygeoapi would use).
    datetime_param = {
        "name": "datetime",
        "in": "query",
        "description": "Either a date-time or an interval (RFC 3339, '..' for open ends). "
                       "Features without temporal properties always match.",
        "required": False,
        "schema": {"type": "string"},
        "style": "form",
        "explode": False,
    }

    # pygeoapi points at sortby.yaml on the *master* branch of ogcapi-records on GitHub. A client that
    # cannot fetch it sees a null parameter (the CITE ETS then fails with a NullPointerException), and
    # the target can change at any time, so the definition (as of 2026-10-30) is inlined.
    sortby_param = {
        "name": "sortby",
        "in": "query",
        "description": "Comma-separated property names to sort by; prefix '+' (ascending, default) or '-' (descending).",
        "required": False,
        "schema": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "[+|-]?[A-Za-z_].*"}},
        "explode": False,
        "style": "form",
    }

    @functools.wraps(original)
    def get_oas_30(cfg: dict, locale: str):
        tags, fragment = original(cfg, locale)
        for path, item in fragment["paths"].items():
            if not (path.startswith("/collections/") and path.endswith("/items")) or "get" not in item:
                continue
            params = item["get"].setdefault("parameters", [])
            params[:] = [dict(sortby_param) if str(p.get("$ref", "")).endswith("/parameters/sortby.yaml") else p
                         for p in params]
            if not any(p.get("name") == "datetime" or str(p.get("$ref", "")).endswith("/datetime") for p in params):
                # /req/core/fc-time-definition: always offered (matches all
                # features of collections without temporal properties).
                params.append(dict(datetime_param))
        return tags, fragment

    itemtypes.get_oas_30 = get_oas_30


def _patch_tileset_errors() -> None:
    from pygeoapi.api import tiles
    from pygeoapi.provider.base import ProviderGenericError

    original = tiles.get_collection_tiles_metadata

    @functools.wraps(original)
    def get_collection_tiles_metadata(api, request, dataset=None, matrix_id=None):
        try:
            return original(api, request, dataset, matrix_id)
        except ProviderGenericError as err:
            headers = request.get_response_headers(**api.api_headers)
            return api.get_exception(err.http_status_code, headers, request.format,
                                     err.ogc_exception_code, err.message)

    tiles.get_collection_tiles_metadata = get_collection_tiles_metadata


def accepts_png(accept: str | None) -> bool:
    """True if an HTTP Accept header admits image/png (absent header admits all)."""
    if not accept or not accept.strip():
        return True
    for media_range in accept.split(","):
        media, *params = [p.strip() for p in media_range.split(";")]
        quality = next((p.split("=", 1)[1] for p in params if p.startswith("q=")), "1")
        try:
            if float(quality) <= 0:
                continue
        except ValueError:
            continue
        if media.lower() in ("image/png", "image/*", "*/*"):
            return True
    return False


def _patch_tile_negotiation() -> None:
    import json

    from pygeoapi.api import tiles
    from pygeoapi.formats import F_JSON

    original = tiles.get_collection_tiles_data

    def ogc_error(api, request, status, code, description):
        headers = request.get_response_headers(**api.api_headers)
        headers["Content-Type"] = "application/json"
        return api.get_exception(status, headers, F_JSON, code, description)

    @functools.wraps(original)
    def get_collection_tiles_data(api, request, dataset=None, matrix_id=None, z_idx=None, y_idx=None, x_idx=None):
        requested = (request.params.get("f") or "").strip().lower()
        if requested and requested != "png":
            return ogc_error(api, request, 400, "InvalidParameterValue", "Only f=png is offered for these tiles")
        if not requested:
            accept = next((v for k, v in request.headers.items() if k.lower() == "accept"), None)
            if not accepts_png(accept):
                return ogc_error(api, request, 406, "NotAcceptable", "Tiles are only available as image/png")
            request._format = "png"
        headers, status, content = original(api, request, dataset, matrix_id, z_idx, y_idx, x_idx)
        if status == 200:
            return headers, status, content
        try:
            body = json.loads(content)
            code, description = body.get("code", "NotFound"), body.get("description", "")
        except (TypeError, ValueError):
            code = "NotFound" if status == 404 else "InvalidParameterValue"
            description = content.decode() if isinstance(content, bytes) else str(content)
        return ogc_error(api, request, status, code, description)

    tiles.get_collection_tiles_data = get_collection_tiles_data


def _patch_geojson_media_type() -> None:
    from pygeoapi.api import itemtypes

    def label_geojson(original):
        @functools.wraps(original)
        def wrapper(api, request, *args, **kwargs):
            headers, status, content = original(api, request, *args, **kwargs)
            if status == 200 and str(headers.get("Content-Type", "")).startswith("application/json"):
                headers["Content-Type"] = "application/geo+json"
            return headers, status, content
        return wrapper

    itemtypes.get_collection_items = label_geojson(itemtypes.get_collection_items)
    itemtypes.get_collection_item = label_geojson(itemtypes.get_collection_item)


def apply(config: dict) -> None:
    """Validate ``config`` and patch pygeoapi in this process (idempotent)."""
    global _applied
    import pygeoapi

    if pygeoapi.__version__ != SUPPORTED_PYGEOAPI:
        raise UnsupportedConfiguration(
            f"conformance adapter is verified for pygeoapi {SUPPORTED_PYGEOAPI}, found {pygeoapi.__version__}")
    validate_config(config)
    if _applied:
        return
    _correct_lists()
    _patch_tiles_openapi()
    _patch_items_openapi()
    _patch_tileset_errors()
    _patch_tile_negotiation()
    _patch_geojson_media_type()
    _applied = True
    LOGGER.info("pygeoapi conformance adapter applied")


def apply_from_env() -> dict:
    from pygeoapi.util import yaml_load

    path = Path(os.environ["PYGEOAPI_CONFIG"])
    with path.open(encoding="utf-8") as fh:
        config = yaml_load(fh)
    apply(config)
    return config
