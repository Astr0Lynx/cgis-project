"""OGC API - Tiles provider for pygeoapi backed by the internal restricted TiTiler.

Publishes PNG *map* tilesets (``dataType: map``) for one catalog raster. The
provider ``data`` is a catalog ``dataset_id``; the tile renderer URL comes only
from the server environment (``CGIS_TILER_INTERNAL_URL``), never from a request,
and TiTiler resolves the dataset to a file itself. Tileset metadata follows OGC
API - Tiles 1.0 /req/tileset (OGC 20-057) and the TMS 2.0 tileset JSON model.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import threading
import time
import zlib
from http import HTTPStatus

import requests
from pygeoapi.models.provider.base import TileMatrixSetEnum
from pygeoapi.provider.base import (
    ProviderConnectionError,
    ProviderGenericError,
    ProviderInvalidQueryError,
)
from pygeoapi.provider.tile import (
    BaseTileProvider,
    ProviderTileNotFoundError,
    ProviderTilesetIdNotFoundError,
)
from pygeoapi.util import url_join

from cgis_ogc.tms import TILE_MATRIX_SETS, matrix_size, tile_matrix_set_limits, tile_range

LOGGER = logging.getLogger(__name__)

DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
REL_TILING_SCHEME = "http://www.opengis.net/def/rel/ogc/1.0/tiling-scheme"
REL_GEODATA = "http://www.opengis.net/def/rel/ogc/1.0/geodata"
REL_CONFORMANCE = "http://www.opengis.net/def/rel/ogc/1.0/conformance"
TILEJSON_TMS = "WebMercatorQuad"
STATS_TTL_SECONDS = 300.0

_stats_cache: dict[str, tuple[float, tuple[float, float]]] = {}
_stats_lock = threading.Lock()


class TileRendererError(ProviderGenericError):
    default_msg = "Tile renderer unavailable"
    http_status_code = HTTPStatus.BAD_GATEWAY
    ogc_exception_code = "ServerError"


def parse_rescale(value) -> list[float] | None:
    """``"lo,hi"`` or ``[lo, hi]`` -> ``[lo, hi]`` (None if unset/invalid)."""
    if not value:
        return None
    parts = value.split(",") if isinstance(value, str) else value
    try:
        lo, hi = (float(p) for p in parts)
    except (TypeError, ValueError):
        LOGGER.warning("ignoring invalid rescale %r", value)
        return None
    return [lo, hi] if hi > lo else None


def _error_code(resp) -> str | None:
    """``error.code`` from the tile service's structured error body, if any."""
    try:
        return resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def blank_png(width: int = 256, height: int = 256) -> bytes:
    """Fully transparent RGBA PNG used for in-range tiles without data."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00" * (4 * width) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


class TiTilerTileProvider(BaseTileProvider):
    """Raster map tiles for one catalog dataset, rendered by TiTiler."""

    def __init__(self, provider_def):
        fmt = provider_def.get("format") or {}
        if fmt.get("name") != "png" or fmt.get("mimetype") != "image/png":
            raise RuntimeError("TiTilerTileProvider only serves image/png map tiles")
        super().__init__(provider_def)
        self.tile_type = "map"  # -> rel tilesets-map, dataType map
        if not isinstance(self.data, str) or not DATASET_ID_RE.match(self.data):
            raise ProviderConnectionError("provider data must be a catalog dataset_id")
        self.dataset_id = self.data
        unknown = [s for s in self.schemes if s not in TILE_MATRIX_SETS]
        if not self.schemes or unknown:
            raise ProviderConnectionError(f"unsupported tile schemes: {unknown or 'none configured'}")
        self.min_zoom = int(self.options["zoom"]["min"])
        self.max_zoom = int(self.options["zoom"]["max"])
        self.bbox = tuple(float(v) for v in self.options["bbox"])
        self.colormap_name = self.options.get("colormap_name", "viridis")
        self.rescale = self.options.get("rescale") or parse_rescale(os.environ.get("OGC_TILE_RESCALE"))
        self.internal_url = os.environ.get("CGIS_TILER_INTERNAL_URL", "http://tiles:8000").rstrip("/")
        self._service_url = None

    def __repr__(self):
        return f"<TiTilerTileProvider> {self.dataset_id}"

    # -- discovery ---------------------------------------------------------
    def get_layer(self):
        return self.dataset_id

    def get_fields(self):
        return {}

    def get_tiling_schemes(self):
        by_id = {e.value.tileMatrixSet: e.value for e in TileMatrixSetEnum}
        return [by_id[s] for s in self.schemes if s in by_id]

    def get_tiles_service(self, baseurl=None, servicepath=None, dirpath=None, tile_type=None):
        url = servicepath if servicepath.startswith(("http://", "https://")) else url_join(baseurl, servicepath)
        self._service_url = url
        return {"links": [{
            "type": self.mimetype,
            "rel": "item",
            "title": f"PNG map tiles of {self.dataset_id} (templated)",
            "href": url,
            "templated": True,
        }]}

    # -- tiles -------------------------------------------------------------
    def _check_tile(self, tileset, z, y, x) -> tuple[int, int, int]:
        if tileset not in self.schemes:
            raise ProviderTilesetIdNotFoundError()
        try:
            z, y, x = int(z), int(y), int(x)
        except (TypeError, ValueError):
            raise ProviderTileNotFoundError("Tile indices must be integers")
        if not self.min_zoom <= z <= self.max_zoom or y < 0 or x < 0:
            raise ProviderTileNotFoundError("Tile matrix, row or column out of range")
        width, height = matrix_size(tileset, z)
        if x >= width or y >= height:
            raise ProviderTileNotFoundError("Tile row or column out of range")
        min_row, max_row, min_col, max_col = tile_range(tileset, z, self.bbox)
        if not (min_row <= y <= max_row and min_col <= x <= max_col):
            raise ProviderTileNotFoundError("Tile outside the tileMatrixSetLimits of this tileset")
        return z, y, x

    def _rescale(self) -> str:
        if self.rescale:
            lo, hi = self.rescale
            return f"{lo:g},{hi:g}"
        now = time.monotonic()
        with _stats_lock:
            hit = _stats_cache.get(self.dataset_id)
        if hit and hit[0] > now:
            lo, hi = hit[1]
        else:
            stats = self._get_json(f"/datasets/{self.dataset_id}/statistics")
            band = next(iter(stats.values()))
            lo, hi = min(0.0, float(band["min"])), float(band["max"])
            if hi <= lo:
                hi = lo + 1.0
            with _stats_lock:
                _stats_cache[self.dataset_id] = (now + STATS_TTL_SECONDS, (lo, hi))
        return f"{lo:g},{hi:g}"

    def _get_json(self, path: str) -> dict:
        try:
            resp = requests.get(self.internal_url + path, timeout=(3, 30))
        except requests.RequestException as err:
            LOGGER.error("tile renderer unreachable: %s", err.__class__.__name__)
            raise TileRendererError()
        if resp.status_code == 404:
            # Dataset withdrawn from the catalog or its asset refused by TiTiler.
            raise ProviderTileNotFoundError("Dataset tiles are not available")
        if resp.status_code != 200:
            LOGGER.error("tile renderer %s -> HTTP %s", path, resp.status_code)
            raise TileRendererError()
        return resp.json()

    def get_tiles(self, layer=None, tileset=None, z=None, y=None, x=None, format_=None):
        z, y, x = self._check_tile(tileset, z, y, x)
        params = {"colormap_name": self.colormap_name, "rescale": self._rescale()}
        url = f"{self.internal_url}/datasets/{self.dataset_id}/tiles/{tileset}/{z}/{x}/{y}.png"
        try:
            resp = requests.get(url, params=params, timeout=(3, 30))
        except requests.RequestException as err:
            LOGGER.error("tile renderer unreachable: %s", err.__class__.__name__)
            raise TileRendererError()
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/png"):
            return resp.content
        code = _error_code(resp)
        if resp.status_code == 404 and code == "TileOutsideBounds":
            # In range of the advertised limits but no pixels (edge rounding):
            # /req/core/tc-error B allows a blank 200 response.
            return blank_png()
        if resp.status_code == 404:
            # Dataset withdrawn from the catalog or its asset refused by TiTiler.
            raise ProviderTileNotFoundError("Dataset tiles are not available")
        LOGGER.error("tile renderer returned HTTP %s (%s) for %s", resp.status_code, code, self.dataset_id)
        raise TileRendererError()

    # -- tileset metadata --------------------------------------------------
    def _links(self, server_url: str, dataset: str, tileset: str) -> list[dict]:
        tileset_url = url_join(server_url, f"collections/{dataset}/tiles/{tileset}")
        links = [
            {"rel": "self", "type": "application/json", "title": "This tileset metadata (JSON)",
             "href": f"{tileset_url}?f=json"},
            {"rel": "alternate", "type": "text/html", "title": "This tileset metadata (HTML)",
             "href": f"{tileset_url}?f=html"},
        ]
        if tileset == TILEJSON_TMS:
            links.append({"rel": "alternate", "type": "application/vnd.mapbox.tile+json",
                          "title": "TileJSON 3.0 rendering of this tileset", "href": f"{tileset_url}?f=tilejson"})
        return links + [
            {"rel": REL_TILING_SCHEME, "type": "application/json",
             "title": f"{tileset} TileMatrixSet definition",
             "href": url_join(server_url, f"TileMatrixSets/{tileset}") + "?f=json"},
            {"rel": "item", "type": self.mimetype, "templated": True,
             "title": f"{tileset} PNG map tiles of {dataset}",
             "href": f"{tileset_url}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}?f=png"},
            {"rel": REL_GEODATA, "type": "application/json", "title": "The collection these tiles represent",
             "href": url_join(server_url, f"collections/{dataset}") + "?f=json"},
            {"rel": REL_CONFORMANCE, "type": "application/json", "title": "Conformance declaration",
             "href": url_join(server_url, "conformance") + "?f=json"},
        ]

    def tileset_metadata(self, server_url, dataset, tileset, title=None, description=None, keywords=None) -> dict:
        crs, tms_uri, _ = TILE_MATRIX_SETS[tileset]
        west, south, east, north = self.bbox
        metadata = {
            "title": f"{title or dataset} ({tileset})",
            "description": description,
            "keywords": keywords,
            "dataType": "map",
            "crs": crs,
            "tileMatrixSetURI": tms_uri,
            "mediaTypes": [self.mimetype],
            "boundingBox": {"lowerLeft": [west, south], "upperRight": [east, north], "crs": CRS84},
            "tileMatrixSetLimits": tile_matrix_set_limits(tileset, self.bbox, self.min_zoom, self.max_zoom),
            "style": self.style(),
            "links": self._links(server_url, dataset, tileset),
        }
        return {k: v for k, v in metadata.items() if v is not None}

    def style(self) -> dict:
        """The single portrayal rule applied to every tile (/req/png/content D)."""
        lo, hi = self._rescale().split(",")
        source = ("configured shared range" if self.rescale
                  else "dataset range (min(0, minimum) to maximum); not comparable across datasets")
        return {
            "id": "default",
            "title": f"{self.colormap_name} linear {lo}..{hi}",
            "description": (f"Cell values {lo}..{hi} map linearly onto the {self.colormap_name} colour ramp "
                            f"(values outside are clamped); NoData cells are transparent. Range: {source}."),
        }

    def tilejson(self, server_url, dataset, tileset, title=None, description=None) -> dict:
        west, south, east, north = self.bbox
        template = url_join(server_url, f"collections/{dataset}/tiles/{tileset}/{{z}}/{{y}}/{{x}}") + "?f=png"
        return {
            "tilejson": "3.0.0",
            "name": title or dataset,
            "description": description,
            "scheme": "xyz",
            "tiles": [template],
            "minzoom": self.min_zoom,
            "maxzoom": self.max_zoom,
            "bounds": [west, south, east, north],
            "center": [(west + east) / 2, (south + north) / 2, max(self.min_zoom, self.max_zoom - 2)],
        }

    def get_metadata(self, dataset, server_url, layer=None, tileset=None, metadata_format=None,
                     title=None, description=None, keywords=None, **kwargs):
        if tileset not in self.schemes:
            raise ProviderTilesetIdNotFoundError()
        fmt = (metadata_format or "json").lower()
        if fmt == "tilejson":
            if tileset != TILEJSON_TMS:
                # TileJSON implies WebMercatorQuad; offering it for other TMSs would mislead clients.
                raise ProviderInvalidQueryError(user_msg=f"TileJSON is only offered for {TILEJSON_TMS}")
            return self.tilejson(server_url, dataset, tileset, title, description)
        if fmt == "html":
            tileset_url = url_join(server_url, f"collections/{dataset}/tiles/{tileset}")
            return {
                "id": dataset,
                "title": title,
                "tileset": tileset,
                "collections_path": f"{tileset_url}/{{tileMatrix}}/{{tileRow}}/{{tileCol}}?f=png",
                "json_url": f"{tileset_url}?f=json",
                "tilejson_url": f"{tileset_url}?f=tilejson",
            }
        return self.tileset_metadata(server_url, dataset, tileset, title, description, keywords)
