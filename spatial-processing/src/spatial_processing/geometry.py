# AOI and CRS parsing/validation, and reprojection of the AOI into the raster's CRS.
# Coordinates are always x/y (lon/lat for geographic CRSs), as in GeoJSON, whatever the
# authority axis order of the declared CRS.

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from .config import ApiError, Settings

_EPSG_RE = re.compile(r"^EPSG:(\d{1,6})$", re.IGNORECASE)
_CRS84_ALIASES = {"OGC:CRS84", "CRS84", "HTTP://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84"}


# Parse `EPSG:<code>` (or an OGC CRS84 alias) naming a 2D geographic/projected CRS; 422
# UNSUPPORTED_CRS otherwise. Used by service._validate (aoi_crs) and service.clip (output_crs).
def parse_crs(text: Any, field: str) -> CRS:
    if not isinstance(text, str):
        raise ApiError(422, "UNSUPPORTED_CRS", f"{field} must be a string like 'EPSG:4326'")
    candidate = text.strip()
    if candidate.upper() in _CRS84_ALIASES:
        return CRS.from_epsg(4326)
    match = _EPSG_RE.match(candidate)
    if not match:
        raise ApiError(
            422,
            "UNSUPPORTED_CRS",
            f"{field} '{text[:64]}' is not supported; use an 'EPSG:<code>' identifier",
            {"field": field},
        )
    try:
        crs = CRS.from_epsg(int(match.group(1)))
    except CRSError:
        raise ApiError(
            422, "UNSUPPORTED_CRS", f"{field} '{candidate}' is not a known EPSG CRS", {"field": field}
        ) from None
    is_2d = len(crs.axis_info) == 2 and not crs.is_compound
    if not (crs.is_geographic or crs.is_projected) or crs.is_geocentric or not is_2d:
        raise ApiError(
            422, "UNSUPPORTED_CRS", f"{field} '{candidate}' must be a 2D geographic or projected CRS", {"field": field}
        )
    return crs


# "EPSG:<code>" for a CRS, or its PROJ string if it has no EPSG code. Used by service results.
def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


# Check a nested GeoJSON coordinate array of the given depth (0 = one position) holds only
# finite numbers; adds the number of positions to counter[0]. Used by parse_aoi.
def _walk_positions(coords: Any, depth: int, counter: list[int]) -> None:
    if not isinstance(coords, (list, tuple)):
        raise ApiError(422, "INVALID_GEOMETRY", "AOI coordinates must be nested arrays")
    if depth == 0:
        if len(coords) not in (2, 3):
            raise ApiError(422, "INVALID_GEOMETRY", "each AOI position must have 2 (or 3) numbers")
        for value in coords:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ApiError(422, "INVALID_GEOMETRY", "AOI coordinates must be numbers")
            if not math.isfinite(value):
                raise ApiError(422, "NON_FINITE_COORDINATES", "AOI coordinates must be finite numbers")
        counter[0] += 1
        return
    for item in coords:
        _walk_positions(item, depth - 1, counter)


# Return a valid, non-empty, 2D (Multi)Polygon in `aoi_crs` from a GeoJSON geometry or Feature,
# enforcing the vertex/part limits; raises ApiError (422/413). Used by service._validate.
def parse_aoi(aoi: Any, aoi_crs: CRS, settings: Settings) -> BaseGeometry:
    if not isinstance(aoi, dict):
        raise ApiError(422, "INVALID_GEOMETRY", "aoi must be a GeoJSON geometry object")
    if aoi.get("type") == "Feature":
        aoi = aoi.get("geometry")
        if not isinstance(aoi, dict):
            raise ApiError(422, "EMPTY_GEOMETRY", "aoi Feature has no geometry")

    gtype = aoi.get("type")
    if gtype not in ("Polygon", "MultiPolygon"):
        raise ApiError(
            422, "UNSUPPORTED_GEOMETRY_TYPE", f"aoi must be a GeoJSON Polygon or MultiPolygon (got {str(gtype)[:32]!r})"
        )
    coords = aoi.get("coordinates")
    if not isinstance(coords, list) or len(coords) == 0:
        raise ApiError(422, "EMPTY_GEOMETRY", "aoi has no coordinates")

    parts = coords if gtype == "MultiPolygon" else [coords]
    if len(parts) > settings.max_aoi_parts:
        raise ApiError(413, "AOI_TOO_COMPLEX", f"aoi has more than {settings.max_aoi_parts} polygon parts")
    counter = [0]
    _walk_positions(coords, 3 if gtype == "MultiPolygon" else 2, counter)
    if counter[0] > settings.max_aoi_vertices:
        raise ApiError(413, "AOI_TOO_COMPLEX", f"aoi has more than {settings.max_aoi_vertices} vertices")
    for polygon in parts:
        if not polygon:
            raise ApiError(422, "EMPTY_GEOMETRY", "aoi contains an empty polygon")
        for ring in polygon:
            if len(ring) < 4:
                raise ApiError(422, "INVALID_GEOMETRY", "each polygon ring needs at least 4 positions")
            if list(ring[0][:2]) != list(ring[-1][:2]):
                raise ApiError(422, "INVALID_GEOMETRY", "polygon rings must be closed (first == last)")

    if aoi_crs.is_geographic:
        for polygon in parts:
            for ring in polygon:
                if any(not (-180.0 <= x <= 180.0 and -90.0 <= y <= 90.0) for x, y, *_ in ring):
                    raise ApiError(
                        422,
                        "COORDINATES_OUT_OF_RANGE",
                        "geographic AOI coordinates must be longitude in [-180, 180] and "
                        "latitude in [-90, 90], in lon/lat order",
                    )

    try:
        geom = shapely.force_2d(shape({"type": gtype, "coordinates": coords}))
    except Exception as exc:  # shapely/GEOS construction errors
        raise ApiError(422, "INVALID_GEOMETRY", "aoi could not be parsed as a polygon") from exc
    if geom.is_empty:
        raise ApiError(422, "EMPTY_GEOMETRY", "aoi geometry is empty")
    if not geom.is_valid:
        raise ApiError(
            422,
            "INVALID_GEOMETRY",
            "aoi geometry is not valid (OGC simple features)",
            {"reason": explain_validity(geom)[:200]},
        )
    if geom.area <= 0:
        raise ApiError(422, "EMPTY_GEOMETRY", "aoi polygon has zero area")
    return geom


# Reproject an AOI from `src` to `dst` (x/y order), densifying edges first so curved edges are
# represented; repairs geometries made invalid by the transform. Used by service._open_selection.
def transform_geometry(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    if src.equals(dst, ignore_axis_order=True):
        return geom
    minx, miny, maxx, maxy = geom.bounds
    max_segment = max(maxx - minx, maxy - miny) / 64.0
    dense = shapely.segmentize(geom, max_segment) if max_segment > 0 else geom
    transformer = Transformer.from_crs(src, dst, always_xy=True)

    # Transform an (N, 2) coordinate array; errcheck makes failures raise. Used by shapely.transform.
    def to_dst(xy):
        x, y = transformer.transform(xy[:, 0], xy[:, 1], errcheck=True)
        return np.column_stack([x, y])

    try:
        out = shapely.transform(dense, to_dst)
    except Exception as exc:
        raise ApiError(422, "INVALID_GEOMETRY", "aoi cannot be transformed into the raster's CRS") from exc
    if out.is_empty or not all(math.isfinite(v) for v in out.bounds):
        raise ApiError(422, "INVALID_GEOMETRY", "aoi cannot be represented in the raster's CRS")
    if not out.is_valid:
        out = shapely.make_valid(out)
        polys = [g for g in getattr(out, "geoms", [out]) if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            raise ApiError(422, "INVALID_GEOMETRY", "aoi becomes degenerate in the raster's CRS")
        out = shapely.union_all(polys)
    return out
