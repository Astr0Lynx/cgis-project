# CRS and AOI validation for exports, reusing spatial-processing's tested rules and error codes (same as
# /api/operations/clip); its ApiError is converted to ours. Used by routes_exports and worker.export.

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from .config import Settings
from .errors import ApiError


# spatial-processing Settings carrying our AOI/window/GDAL cache limits. Used by parse_aoi.
def _processing_settings(settings: Settings):
    from spatial_processing.config import Settings as ProcessingSettings

    return ProcessingSettings(max_aoi_vertices=settings.max_aoi_vertices, max_aoi_parts=settings.max_aoi_parts,
                              max_window_pixels=settings.export_clip_max_pixels,
                              gdal_cachemax_mb=settings.gdal_cachemax_mb)


# Re-raise spatial-processing's ApiError as cgis_app ApiError (same status/code/details). Used by the wrappers below.
@contextmanager
def _as_api_error() -> Iterator[None]:
    from spatial_processing.config import ApiError as ProcessingError

    try:
        yield
    except ProcessingError as exc:
        raise ApiError(exc.status, exc.code, exc.message, dict(exc.details or {})) from None


# Parse 'EPSG:<code>' into a 2D pyproj CRS (422 UNSUPPORTED_CRS otherwise). Used by routes_exports.create_export.
def parse_crs(text: Any, field: str) -> CRS:
    from spatial_processing import geometry

    with _as_api_error():
        return geometry.parse_crs(text, field)


# 'EPSG:<code>' for a pyproj or rasterio CRS, else its string form. Used by routes_exports and worker.export.
def crs_label(crs) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


# Validate a GeoJSON Polygon/MultiPolygon AOI with vertex/part caps. Used by routes_exports.create_export.
def parse_aoi(aoi: Any, aoi_crs: CRS, settings: Settings) -> BaseGeometry:
    from spatial_processing import geometry

    with _as_api_error():
        return geometry.parse_aoi(aoi, aoi_crs, _processing_settings(settings))


# Reproject a geometry between CRSs (422 when impossible). Used by routes_exports.create_export.
def transform_geometry(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    from spatial_processing import geometry

    with _as_api_error():
        return geometry.transform_geometry(geom, src, dst)
