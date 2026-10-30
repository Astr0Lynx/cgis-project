"""AOI parsing, validation and CRS handling.

Coordinates are always interpreted in x/y order (longitude, latitude for geographic
CRSs), as in GeoJSON, regardless of the authority axis order of the declared CRS.
"""

from __future__ import annotations

import math
import re
from typing import Any

import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from . import errors
from .config import Settings
from .errors import ApiError

_EPSG_RE = re.compile(r"^EPSG:(\d{1,6})$", re.IGNORECASE)
_CRS84_ALIASES = {
    "OGC:CRS84",
    "CRS84",
    "HTTP://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84",
}


def parse_crs(text: Any, field: str) -> CRS:
    """Accept only ``EPSG:<code>`` (or OGC CRS84) naming a 2D geographic/projected CRS."""
    if not isinstance(text, str):
        raise ApiError(422, errors.UNSUPPORTED_CRS, f"{field} must be a string like 'EPSG:4326'")
    candidate = text.strip()
    if candidate.upper() in _CRS84_ALIASES:
        return CRS.from_epsg(4326)
    match = _EPSG_RE.match(candidate)
    if not match:
        raise ApiError(
            422,
            errors.UNSUPPORTED_CRS,
            f"{field} '{text[:64]}' is not supported; use an 'EPSG:<code>' identifier",
            {"field": field},
        )
    try:
        crs = CRS.from_epsg(int(match.group(1)))
    except CRSError:
        raise ApiError(
            422, errors.UNSUPPORTED_CRS, f"{field} '{candidate}' is not a known EPSG CRS", {"field": field}
        ) from None
    is_2d = len(crs.axis_info) == 2 and not crs.is_compound
    if not (crs.is_geographic or crs.is_projected) or crs.is_geocentric or not is_2d:
        raise ApiError(
            422,
            errors.UNSUPPORTED_CRS,
            f"{field} '{candidate}' must be a 2D geographic or projected CRS",
            {"field": field},
        )
    return crs


def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


def _walk_positions(coords: Any, depth: int, counter: list[int]) -> None:
    """Validate a nested GeoJSON coordinate array of the given nesting depth."""
    if not isinstance(coords, (list, tuple)):
        raise ApiError(422, errors.INVALID_GEOMETRY, "AOI coordinates must be nested arrays")
    if depth == 0:
        if len(coords) not in (2, 3):
            raise ApiError(422, errors.INVALID_GEOMETRY, "each AOI position must have 2 (or 3) numbers")
        for value in coords:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ApiError(422, errors.INVALID_GEOMETRY, "AOI coordinates must be numbers")
            if not math.isfinite(value):
                raise ApiError(422, errors.NON_FINITE_COORDINATES, "AOI coordinates must be finite numbers")
        counter[0] += 1
        return
    for item in coords:
        _walk_positions(item, depth - 1, counter)


def parse_aoi(aoi: Any, aoi_crs: CRS, settings: Settings) -> BaseGeometry:
    """Return a valid, non-empty (Multi)Polygon in ``aoi_crs`` or raise ApiError."""
    if not isinstance(aoi, dict):
        raise ApiError(422, errors.INVALID_GEOMETRY, "aoi must be a GeoJSON geometry object")
    if aoi.get("type") == "Feature":
        aoi = aoi.get("geometry")
        if not isinstance(aoi, dict):
            raise ApiError(422, errors.EMPTY_GEOMETRY, "aoi Feature has no geometry")

    gtype = aoi.get("type")
    if gtype not in ("Polygon", "MultiPolygon"):
        raise ApiError(
            422,
            errors.UNSUPPORTED_GEOMETRY_TYPE,
            f"aoi must be a GeoJSON Polygon or MultiPolygon (got {str(gtype)[:32]!r})",
        )
    coords = aoi.get("coordinates")
    if not isinstance(coords, list) or len(coords) == 0:
        raise ApiError(422, errors.EMPTY_GEOMETRY, "aoi has no coordinates")

    parts = coords if gtype == "MultiPolygon" else [coords]
    if len(parts) > settings.max_aoi_parts:
        raise ApiError(
            413, errors.AOI_TOO_COMPLEX, f"aoi has more than {settings.max_aoi_parts} polygon parts"
        )
    counter = [0]
    _walk_positions(coords, 3 if gtype == "MultiPolygon" else 2, counter)
    if counter[0] > settings.max_aoi_vertices:
        raise ApiError(
            413, errors.AOI_TOO_COMPLEX, f"aoi has more than {settings.max_aoi_vertices} vertices"
        )
    for polygon in parts:
        if not polygon:
            raise ApiError(422, errors.EMPTY_GEOMETRY, "aoi contains an empty polygon")
        for ring in polygon:
            if len(ring) < 4:
                raise ApiError(422, errors.INVALID_GEOMETRY, "each polygon ring needs at least 4 positions")
            if list(ring[0][:2]) != list(ring[-1][:2]):
                raise ApiError(422, errors.INVALID_GEOMETRY, "polygon rings must be closed (first == last)")

    if aoi_crs.is_geographic:
        for polygon in parts:
            for ring in polygon:
                for pos in ring:
                    if not (-180.0 <= pos[0] <= 180.0 and -90.0 <= pos[1] <= 90.0):
                        raise ApiError(
                            422,
                            errors.COORDINATES_OUT_OF_RANGE,
                            "geographic AOI coordinates must be longitude in [-180, 180] and "
                            "latitude in [-90, 90], in lon/lat order",
                        )

    try:
        geom = shape({"type": gtype, "coordinates": coords})
    except Exception as exc:  # shapely/GEOS construction errors
        raise ApiError(422, errors.INVALID_GEOMETRY, "aoi could not be parsed as a polygon") from exc
    geom = shapely.force_2d(geom)
    if geom.is_empty:
        raise ApiError(422, errors.EMPTY_GEOMETRY, "aoi geometry is empty")
    if not geom.is_valid:
        raise ApiError(
            422,
            errors.INVALID_GEOMETRY,
            "aoi geometry is not valid (OGC simple features)",
            {"reason": explain_validity(geom)[:200]},
        )
    if geom.area <= 0:
        raise ApiError(422, errors.EMPTY_GEOMETRY, "aoi polygon has zero area")
    return geom


def transform_geometry(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    """Reproject an AOI, densifying edges first so curved edges are represented."""
    if src.equals(dst, ignore_axis_order=True):  # x/y order is fixed via always_xy below
        return geom
    minx, miny, maxx, maxy = geom.bounds
    max_segment = max(maxx - minx, maxy - miny) / 64.0
    dense = shapely.segmentize(geom, max_segment) if max_segment > 0 else geom
    transformer = Transformer.from_crs(src, dst, always_xy=True)
    try:
        out = shapely.transform(dense, lambda xy: _xy_transform(transformer, xy))
    except Exception as exc:
        raise ApiError(
            422, errors.INVALID_GEOMETRY, "aoi cannot be transformed into the raster's CRS"
        ) from exc
    if out.is_empty or not all(math.isfinite(v) for v in out.bounds):
        raise ApiError(422, errors.INVALID_GEOMETRY, "aoi cannot be represented in the raster's CRS")
    if not out.is_valid:
        out = shapely.make_valid(out)
        polys = [g for g in getattr(out, "geoms", [out]) if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            raise ApiError(422, errors.INVALID_GEOMETRY, "aoi becomes degenerate in the raster's CRS")
        out = shapely.union_all(polys)
    return out


def _xy_transform(transformer: Transformer, xy):
    import numpy as np

    x, y = transformer.transform(xy[:, 0], xy[:, 1], errcheck=True)
    return np.column_stack([x, y])
