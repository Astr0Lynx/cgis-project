"""GHSL 100 m Mollweide tile index (verified against R6_C26: origin x=6,959,000 y=4,000,000, 10000x10000 px).

Rows R1..R18 run north->south, columns C1..C36 west->east, tiles are 1000 km square.
"""
from __future__ import annotations

import math
from pyproj import Transformer

X0, Y0, TILE_M, NROWS, NCOLS = -18_041_000.0, 9_000_000.0, 1_000_000.0, 18, 36
_T = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)


def tile_origin(row: int, col: int) -> tuple[float, float]:
    return X0 + (col - 1) * TILE_M, Y0 - (row - 1) * TILE_M


def tiles_for_bbox(bbox_wgs84: tuple[float, float, float, float], densify: int = 25) -> list[tuple[int, int]]:
    """(row, col) tiles intersecting a lon/lat bbox (edges densified because Mollweide is curved)."""
    w, s, e, n = bbox_wgs84
    pts = []
    for i in range(densify + 1):
        f = i / densify
        pts += [(w + f * (e - w), s), (w + f * (e - w), n), (w, s + f * (n - s)), (e, s + f * (n - s))]
    xs, ys = zip(*(_T.transform(x, y) for x, y in pts))
    c0, c1 = math.floor((min(xs) - X0) / TILE_M) + 1, math.floor((max(xs) - X0) / TILE_M) + 1
    r0, r1 = math.floor((Y0 - max(ys)) / TILE_M) + 1, math.floor((Y0 - min(ys)) / TILE_M) + 1
    return [(r, c) for r in range(max(r0, 1), min(r1, NROWS) + 1) for c in range(max(c0, 1), min(c1, NCOLS) + 1)]


INDIA_BBOX = (68.0, 6.5, 97.5, 37.2)        # mainland + Andaman/Nicobar excluded? see below
# Andaman & Nicobar (92.2-94.3E, 6.7-13.7N) lie inside this bbox; Lakshadweep (71.5-74.0E, 8.0-12.5N) too.


# ---- EPSG:4326 3 arc-second product (the primary GHSL grid) -------------------------------------------------
# Verified on R7_C26: origin lon 69.99208307850779, lat 29.099583419121082, 12000x12000 px, pixel 0.00083333333003 deg.
# The origin is an integer number of pixels from WorldPop-IND's (68.185416408, 35.501250059): 2168 cols, 7682 rows.
PIX_4326 = 0.00083333333003
TILE_PX_4326 = 12000
_R7C26 = (69.99208307850779, 29.099583419121082)
TILE_DEG_4326 = TILE_PX_4326 * PIX_4326


def tile_origin_4326(row: int, col: int) -> tuple[float, float]:
    return _R7C26[0] + (col - 26) * TILE_DEG_4326, _R7C26[1] - (row - 7) * TILE_DEG_4326


def tiles_for_bbox_4326(bbox_wgs84: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    w, s, e, n = bbox_wgs84
    c0, c1 = (math.floor((x - _R7C26[0]) / TILE_DEG_4326) + 26 for x in (w, e))
    r0, r1 = (7 + math.floor((_R7C26[1] - y) / TILE_DEG_4326) for y in (n, s))
    return [(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]
