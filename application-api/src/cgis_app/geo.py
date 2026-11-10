"""AOI and CRS validation, reusing the tested spatial-processing implementation (same rules and error codes as
``/api/operations/clip``): EPSG-only 2D CRSs, GeoJSON Polygon/MultiPolygon in x/y order, vertex/part caps."""

from __future__ import annotations

from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from .config import Settings
from .errors import ApiError


def _processing_settings(settings: Settings):
    from spatial_processing.config import Settings as ProcessingSettings

    return ProcessingSettings(max_aoi_vertices=settings.max_aoi_vertices, max_aoi_parts=settings.max_aoi_parts,
                              max_window_pixels=settings.export_clip_max_pixels,
                              gdal_cachemax_mb=settings.gdal_cachemax_mb)


def _convert(exc) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, dict(exc.details or {}))


def parse_crs(text: Any, field: str) -> CRS:
    from spatial_processing import geometry
    from spatial_processing.errors import ApiError as ProcessingError

    try:
        return geometry.parse_crs(text, field)
    except ProcessingError as exc:
        raise _convert(exc) from None


def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


def parse_aoi(aoi: Any, aoi_crs: CRS, settings: Settings) -> BaseGeometry:
    from spatial_processing import geometry
    from spatial_processing.errors import ApiError as ProcessingError

    try:
        return geometry.parse_aoi(aoi, aoi_crs, _processing_settings(settings))
    except ProcessingError as exc:
        raise _convert(exc) from None


def transform_geometry(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    from spatial_processing import geometry
    from spatial_processing.errors import ApiError as ProcessingError

    try:
        return geometry.transform_geometry(geom, src, dst)
    except ProcessingError as exc:
        raise _convert(exc) from None
