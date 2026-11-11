# Tile-matrix arithmetic for the two registered TileMatrixSets we publish (OGC 17-083r4 Annex D,
# 256x256 tiles): tileMatrixSetLimits, tile range checks and the native max zoom of a raster.

from __future__ import annotations

import math

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
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
        CRS84,
        "http://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad",
        23,
    ),
}


# (matrixWidth, matrixHeight) of one tile matrix. Used by tile_range and TiTilerTileProvider._check_tile.
def matrix_size(tms_id: str, zoom: int) -> tuple[int, int]:
    if tms_id == "WebMercatorQuad":
        return 2 ** zoom, 2 ** zoom
    if tms_id == "WorldCRS84Quad":
        return 2 ** (zoom + 1), 2 ** zoom
    raise KeyError(tms_id)


# Position of a lon/lat as fractions (0..1) of the matrix width/height from the top-left origin.
# Used by tile_range.
def _fraction(tms_id: str, lon: float, lat: float) -> tuple[float, float]:
    if tms_id == "WebMercatorQuad":
        lat = max(-WEBMERCATOR_MAX_LAT, min(WEBMERCATOR_MAX_LAT, lat))
        x = math.radians(lon) * EARTH_RADIUS
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * EARTH_RADIUS
        return (x + WEBMERCATOR_HALF) / (2 * WEBMERCATOR_HALF), (WEBMERCATOR_HALF - y) / (2 * WEBMERCATOR_HALF)
    if tms_id == "WorldCRS84Quad":
        return (lon + 180.0) / 360.0, (90.0 - lat) / 180.0
    raise KeyError(tms_id)


# (minTileRow, maxTileRow, minTileCol, maxTileCol) covering a CRS84 bbox.
# Used by tile_matrix_set_limits and TiTilerTileProvider._check_tile.
def tile_range(tms_id: str, zoom: int, bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    west, south, east, north = bbox
    width, height = matrix_size(tms_id, zoom)
    eps = 1e-9
    fx_min, fy_min = _fraction(tms_id, west, north)
    fx_max, fy_max = _fraction(tms_id, east, south)

    # Clamps an index into 0..hi-1.
    def clamp(v: int, hi: int) -> int:
        return max(0, min(hi - 1, v))

    min_col = clamp(math.floor(fx_min * width + eps), width)
    max_col = clamp(math.ceil(fx_max * width - eps) - 1, width)
    min_row = clamp(math.floor(fy_min * height + eps), height)
    max_row = clamp(math.ceil(fy_max * height - eps) - 1, height)
    return min_row, max(min_row, max_row), min_col, max(min_col, max_col)


# tileMatrixSetLimits entries for zooms min_zoom..max_zoom. Used by TiTilerTileProvider.tileset_metadata.
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


# First WebMercatorQuad zoom whose cell is <= the native resolution, plus overzoom; `cap` if unknown.
# Used by publish.raster_resource.
def native_max_zoom(resolution: float | None, geographic: bool, overzoom: int = 2, cap: int = 22) -> int:
    if not resolution or resolution <= 0:
        return cap
    metres = resolution * (math.pi * EARTH_RADIUS / 180.0) if geographic else resolution
    z0_cell = 2 * WEBMERCATOR_HALF / TILE_SIZE
    zoom = math.ceil(math.log2(z0_cell / metres))
    return max(0, min(cap, zoom + overzoom))
