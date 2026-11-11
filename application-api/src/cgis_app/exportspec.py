"""Export formats, CRS rules and output naming shared by the API and the export worker (no FastAPI).

Only formats that are implemented AND verified by tests are offered:

* raster: ``GTiff`` (tiled, DEFLATE, lossless) and ``COG`` (Cloud-Optimized GeoTIFF);
* vector: ``GeoJSON`` (RFC 7946 when the output CRS is EPSG:4326).

Reprojection uses nearest-neighbour resampling: cell values are copied, never area-weighted, so the
totals of count rasters (people_per_cell) are NOT preserved by a reprojected export. Native-CRS exports
are exact.
"""

from __future__ import annotations

import re

FORMATS = {
    "raster": [
        {"id": "GTiff", "label": "GeoTIFF (tiled, DEFLATE, lossless)", "media_type": "image/tiff; application=geotiff",
         "extension": "tif"},
        {"id": "COG", "label": "Cloud-Optimized GeoTIFF", "extension": "tif",
         "media_type": "image/tiff; application=geotiff; profile=cloud-optimized"},
    ],
    "vector": [
        {"id": "GeoJSON", "label": "GeoJSON (RFC 7946 in EPSG:4326)", "media_type": "application/geo+json",
         "extension": "geojson"},
    ],
}
_ALIASES = {
    "raster": {"gtiff": "GTiff", "geotiff": "GTiff", "tif": "GTiff", "tiff": "GTiff", "cog": "COG"},
    "vector": {"geojson": "GeoJSON", "json": "GeoJSON"},
}
MEDIA_TYPES = {f["id"]: f["media_type"] for fs in FORMATS.values() for f in fs}
EXTENSIONS = {f["id"]: f["extension"] for fs in FORMATS.values() for f in fs}

SUGGESTED_CRS = [
    {"id": "EPSG:4326", "label": "WGS 84 (longitude/latitude)"},
    {"id": "EPSG:3857", "label": "WGS 84 / Pseudo-Mercator (web maps; not equal-area)"},
    {"id": "EPSG:24378", "label": "Kalianpur 1975 / India zone I"},
    {"id": "EPSG:32643", "label": "WGS 84 / UTM zone 43N"},
    {"id": "EPSG:32644", "label": "WGS 84 / UTM zone 44N"},
    {"id": "EPSG:32645", "label": "WGS 84 / UTM zone 45N"},
    {"id": "EPSG:6933", "label": "WGS 84 / NSIDC EASE-Grid 2.0 Global (equal-area)"},
]

RESAMPLING_WARNING = ("Reprojected with nearest-neighbour resampling onto a new grid: cell values are copied, not "
                      "area-weighted, so totals of count rasters (e.g. people_per_cell) are NOT preserved. Export in "
                      "the native CRS for analysis.")
GEOJSON_CRS_WARNING = ("GeoJSON in a CRS other than EPSG:4326 is not RFC 7946 compliant; a legacy 'crs' member names "
                       "the CRS (read by GDAL/QGIS). Use EPSG:4326 for maximum interoperability.")


def normalize_format(kind: str, fmt: str) -> str | None:
    return _ALIASES.get(kind, {}).get((fmt or "").strip().lower())


def supported_ids(kind: str) -> list[str]:
    return [f["id"] for f in FORMATS.get(kind, [])]


def capabilities() -> dict:
    return {
        "kinds": ["full", "clip"],
        "formats": FORMATS,
        "output_crs": {"suggested": SUGGESTED_CRS, "accepts_any_epsg": True, "native_default": True,
                       "rules": "EPSG:<code> naming a 2D geographic or projected CRS. Omit for the native CRS (exact)."},
        "aoi": {"crs_default": "EPSG:4326", "geometry_types": ["Polygon", "MultiPolygon", "Feature"],
                "pixel_selection": "raster clip keeps pixels whose CENTER lies in the AOI (all_touched=false)",
                "vector_clip": "features intersecting the AOI, geometries cut to the AOI"},
        "resampling": "nearest (only when output_crs differs from the native CRS)",
        "notes": [RESAMPLING_WARNING, GEOJSON_CRS_WARNING],
    }


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def output_filename(dataset_id: str, version: int, kind: str, fmt: str, crs_label: str | None, job_hex: str) -> str:
    crs_part = ""
    if crs_label:
        crs_part = "_" + _SAFE.sub("", crs_label.replace(":", "")).lower()
    base = f"{_SAFE.sub('-', dataset_id)}_v{version}_{kind}{crs_part}_{job_hex[:8]}"
    return f"{base[:180]}.{EXTENSIONS[fmt]}"
