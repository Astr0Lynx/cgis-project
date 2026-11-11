# Standards adapter for pygeoapi 0.24.0: corrects the advertised conformance classes and a few protocol
# details (tiles OpenAPI, datetime/sortby params, tile negotiation, GeoJSON media type), and refuses to
# start on configurations where the corrected declaration would be untrue. Used by wsgi and publish.

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


# Raised when the config or pygeoapi version falls outside what the adapter is verified for.
class UnsupportedConfiguration(RuntimeError):
    pass


# Checks the invariants that make the corrections true: only read-only collections with our PNG tile
# provider or our feature provider. Used by apply and the unit tests.
def validate_config(config: dict) -> None:
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


# Drops WITHDRAWN classes from pygeoapi's conformance lists and adds the PNG class. Used by apply.
def _correct_lists() -> None:
    import pygeoapi.api as core_api
    from pygeoapi.api import itemtypes, tiles

    for module, attr in ((core_api, "CONFORMANCE_CLASSES"), (tiles, "CONFORMANCE_CLASSES"),
                         (itemtypes, "CONFORMANCE_CLASSES_FEATURES")):
        current = getattr(module, attr)
        current[:] = [c for c in current if c not in WITHDRAWN]
    if TILES_PNG not in tiles.CONFORMANCE_CLASSES:
        tiles.CONFORMANCE_CLASSES.append(TILES_PNG)


# Adds the /collections/{id}/tiles/{tileMatrixSetId} path pygeoapi serves but omits from OpenAPI
# (/req/oas30/completeness). Used by apply.
def _patch_tiles_openapi() -> None:
    from pygeoapi.api import tiles
    from pygeoapi.openapi import OPENAPI_YAML

    original = tiles.get_oas_30

    # Wraps tiles.get_oas_30: adds one tileset path per tiles path.
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


# Declares `datetime` on every items path and inlines `sortby` (pygeoapi $refs a GitHub master file).
# Used by apply.
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

    # Wraps itemtypes.get_oas_30: fixes the parameters of every items path.
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


# Returns provider errors in tileset metadata as OGC exceptions instead of HTTP 500. Used by apply.
def _patch_tileset_errors() -> None:
    from pygeoapi.api import tiles
    from pygeoapi.provider.base import ProviderGenericError

    original = tiles.get_collection_tiles_metadata

    # Wraps tiles.get_collection_tiles_metadata: provider errors -> OGC exception response.
    @functools.wraps(original)
    def get_collection_tiles_metadata(api, request, dataset=None, matrix_id=None):
        try:
            return original(api, request, dataset, matrix_id)
        except ProviderGenericError as err:
            headers = request.get_response_headers(**api.api_headers)
            return api.get_exception(err.http_status_code, headers, request.format,
                                     err.ogc_exception_code, err.message)

    tiles.get_collection_tiles_metadata = get_collection_tiles_metadata


# True if an HTTP Accept header admits image/png (an absent header admits all).
# Used by _patch_tile_negotiation and the unit tests.
def accepts_png(accept: str | None) -> bool:
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


# Tile requests: PNG by Accept negotiation when no `f`, 406/400 for other formats, and JSON-labelled
# error responses instead of image/png. Used by apply.
def _patch_tile_negotiation() -> None:
    import json

    from pygeoapi.api import tiles
    from pygeoapi.formats import F_JSON

    original = tiles.get_collection_tiles_data

    # JSON OGC exception response. Used by get_collection_tiles_data below.
    def ogc_error(api, request, status, code, description):
        headers = request.get_response_headers(**api.api_headers)
        headers["Content-Type"] = "application/json"
        return api.get_exception(status, headers, F_JSON, code, description)

    # Wraps tiles.get_collection_tiles_data: format negotiation and JSON error responses.
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


# Labels JSON items/item responses as application/geo+json. Used by apply.
def _patch_geojson_media_type() -> None:
    from pygeoapi.api import itemtypes

    # Wraps an items/item handler so successful JSON responses say application/geo+json.
    def label_geojson(original):
        # Relabels successful JSON responses.
        @functools.wraps(original)
        def wrapper(api, request, *args, **kwargs):
            headers, status, content = original(api, request, *args, **kwargs)
            if status == 200 and str(headers.get("Content-Type", "")).startswith("application/json"):
                headers["Content-Type"] = "application/geo+json"
            return headers, status, content
        return wrapper

    itemtypes.get_collection_items = label_geojson(itemtypes.get_collection_items)
    itemtypes.get_collection_item = label_geojson(itemtypes.get_collection_item)


# Checks the pygeoapi version and config, then patches pygeoapi in this process (idempotent).
# Used by apply_from_env, publish.write_openapi and the unit tests.
def apply(config: dict) -> None:
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


# Loads $PYGEOAPI_CONFIG and applies the adapter to it. Used by wsgi.
def apply_from_env() -> dict:
    from pygeoapi.util import yaml_load

    path = Path(os.environ["PYGEOAPI_CONFIG"])
    with path.open(encoding="utf-8") as fh:
        config = yaml_load(fh)
    apply(config)
    return config
