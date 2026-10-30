"""Tile-matrix arithmetic for the two OGC-registered TileMatrixSets we publish.

Used for ``tileMatrixSetLimits`` in OGC API - Tiles tileset metadata and to
decide whether a requested tile is in range. Definitions follow OGC 17-083r4
Annex D (WebMercatorQuad, WorldCRS84Quad) with 256x256 tiles.
"""

from __future__ import annotations

import math

EARTH_RADIUS = 6378137.0
WEBMERCATOR_MAX_LAT = 85.0511287798066
WEBMERCATOR_HALF = math.pi * EARTH_RADIUS  # 20037508.342789244
TILE_SIZE = 256

# id -> (crs URI, registered TMS URI, maximum tile matrix id supported)
TILE_MATRIX_SETS = {
    "WebMercatorQuad": (
        "http://www.opengis.net/def/crs/EPSG/0/3857",
        "http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad",
        24,
    ),
    "WorldCRS84Quad": (
        "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "http://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad",
        23,
    ),
}


def matrix_size(tms_id: str, zoom: int) -> tuple[int, int]:
    """(matrixWidth, matrixHeight) of a tile matrix."""
    if tms_id == "WebMercatorQuad":
        return 2 ** zoom, 2 ** zoom
    if tms_id == "WorldCRS84Quad":
        return 2 ** (zoom + 1), 2 ** zoom
    raise KeyError(tms_id)


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-WEBMERCATOR_MAX_LAT, min(WEBMERCATOR_MAX_LAT, lat))
    x = math.radians(lon) * EARTH_RADIUS
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * EARTH_RADIUS
    return x, y


def _fraction(tms_id: str, lon: float, lat: float) -> tuple[float, float]:
    """Position as fractions (0..1) of the matrix width/height from the top-left origin."""
    if tms_id == "WebMercatorQuad":
        x, y = _mercator(lon, lat)
        return (x + WEBMERCATOR_HALF) / (2 * WEBMERCATOR_HALF), (WEBMERCATOR_HALF - y) / (2 * WEBMERCATOR_HALF)
    if tms_id == "WorldCRS84Quad":
        return (lon + 180.0) / 360.0, (90.0 - lat) / 180.0
    raise KeyError(tms_id)


def tile_range(tms_id: str, zoom: int, bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """(minTileRow, maxTileRow, minTileCol, maxTileCol) covering a CRS84 bbox."""
    west, south, east, north = bbox
    width, height = matrix_size(tms_id, zoom)
    eps = 1e-9
    fx_min, fy_min = _fraction(tms_id, west, north)
    fx_max, fy_max = _fraction(tms_id, east, south)

    def clamp(v: int, hi: int) -> int:
        return max(0, min(hi - 1, v))

    min_col = clamp(math.floor(fx_min * width + eps), width)
    max_col = clamp(math.ceil(fx_max * width - eps) - 1, width)
    min_row = clamp(math.floor(fy_min * height + eps), height)
    max_row = clamp(math.ceil(fy_max * height - eps) - 1, height)
    return min_row, max(min_row, max_row), min_col, max(min_col, max_col)


def tile_matrix_set_limits(tms_id: str, bbox, min_zoom: int, max_zoom: int) -> list[dict]:
    limits = []
    for z in range(min_zoom, max_zoom + 1):
        min_row, max_row, min_col, max_col = tile_range(tms_id, z, bbox)
        limits.append({
            "tileMatrix": str(z),
            "minTileRow": min_row, "maxTileRow": max_row,
            "minTileCol": min_col, "maxTileCol": max_col,
        })
    return limits


def native_max_zoom(resolution: float | None, geographic: bool, overzoom: int = 2, cap: int = 22) -> int:
    """Smallest WebMercatorQuad zoom whose cell size is <= the native resolution,
    plus a small overzoom allowance. Falls back to ``cap`` when unknown."""
    if not resolution or resolution <= 0:
        return cap
    metres = resolution * (math.pi * EARTH_RADIUS / 180.0) if geographic else resolution
    z0_cell = 2 * WEBMERCATOR_HALF / TILE_SIZE
    zoom = math.ceil(math.log2(z0_cell / metres))
    return max(0, min(cap, zoom + overzoom))
