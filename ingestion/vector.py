"""GeoJSON polygon validation, reprojection to EPSG:4326 and MultiPolygon normalization.

Input CRS: RFC 7946 GeoJSON is WGS84 lon/lat. A legacy (2008) ``crs`` member such as
``urn:ogc:def:crs:EPSG::32644`` is honoured and the geometries are transformed to EPSG:4326
with lon/lat axis order. Invalid geometries are rejected, never silently repaired.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from .errors import ValidationError
from .manifest import DatasetSpec

WGS84 = CRS.from_epsg(4326)
_CRS84_NAMES = {"urn:ogc:def:crs:ogc:1.3:crs84", "urn:ogc:def:crs:ogc::crs84", "crs84", "ogc:crs84"}


@dataclass
class UnitFeature:
    unit_id: str
    name: str
    level: str | None
    geom: MultiPolygon
    properties: dict[str, Any]


@dataclass
class VectorInfo:
    features: list[UnitFeature]
    source_crs: str
    bbox_wgs84: list[float]
    stats: dict[str, Any] = field(default_factory=dict)

    def to_provenance(self, spec: DatasetSpec) -> dict[str, Any]:
        return {
            "feature_count": len(self.features),
            "unit_ids": [f.unit_id for f in self.features],
            "source_crs": self.source_crs,
            "stored_crs": "EPSG:4326",
            "geometry_type": "MultiPolygon",
            "id_property": spec.id_property,
            "name_property": spec.name_property,
            "level_property": spec.level_property,
            "unit_id_prefix": spec.unit_id_prefix,
        }

    def to_geojson_bytes(self, spec: DatasetSpec) -> bytes:
        """Deterministic normalized FeatureCollection (EPSG:4326, RFC 7946 winding)."""
        fc = {
            "type": "FeatureCollection",
            "name": spec.dataset_id,
            "dataset_id": spec.dataset_id,
            "dataset_version": spec.version,
            "title": spec.title,
            "features": [
                {
                    "type": "Feature",
                    "id": f.unit_id,
                    "properties": {**f.properties, "unit_id": f.unit_id, "name": f.name, "level": f.level},
                    "geometry": mapping(f.geom),
                }
                for f in self.features
            ],
        }
        return (json.dumps(fc, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _source_crs(doc: dict[str, Any], name: str) -> CRS:
    member = doc.get("crs")
    if member is None:
        return WGS84
    try:
        crs_name = member["properties"]["name"]
    except (TypeError, KeyError) as exc:
        raise ValidationError(f"{name} has an unsupported 'crs' member; use a named CRS", code="unsupported_crs") \
            from exc
    if str(crs_name).strip().lower() in _CRS84_NAMES:
        return WGS84
    try:
        crs = CRS.from_user_input(crs_name)
    except CRSError as exc:
        raise ValidationError(f"{name} declares an unknown CRS {crs_name!r}", code="unsupported_crs") from exc
    if crs.to_epsg() is None:
        raise ValidationError(f"{name} CRS {crs_name!r} has no EPSG code", code="unsupported_crs")
    return crs


def read_vector(spec: DatasetSpec) -> VectorInfo:
    name = spec.file.name
    if spec.file.suffix.lower() not in (".geojson", ".json"):
        raise ValidationError(f"{name}: only GeoJSON vector sources are supported in v1", code="unsupported_format")
    try:
        doc = json.loads(Path(spec.file).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{name} is not valid GeoJSON: {exc}", code="corrupt_vector") from exc
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection" or not isinstance(doc.get("features"),
                                                                                              list):
        raise ValidationError(f"{name} must be a GeoJSON FeatureCollection", code="corrupt_vector")
    if not doc["features"]:
        raise ValidationError(f"{name} contains no features", code="empty_vector")

    crs = _source_crs(doc, name)
    transformer = None if crs.equals(WGS84, ignore_axis_order=True) else Transformer.from_crs(crs, WGS84,
                                                                                              always_xy=True)
    features: list[UnitFeature] = []
    seen: set[str] = set()
    for i, feat in enumerate(doc["features"]):
        features.append(_parse_feature(feat, i, spec, transformer, seen, name))

    union_bounds = shapely.total_bounds([f.geom for f in features]).tolist()
    exp_count = spec.expect.get("feature_count")
    if exp_count is not None and exp_count != len(features):
        raise ValidationError(f"{name} has {len(features)} features; manifest expects {exp_count}",
                              code="expectation_mismatch")
    epsg = crs.to_epsg()
    return VectorInfo(features=features, source_crs=f"EPSG:{epsg}" if epsg else crs.to_string(),
                      bbox_wgs84=union_bounds, stats={"feature_count": len(features)})


def _parse_feature(feat: Any, i: int, spec: DatasetSpec, transformer, seen: set[str], name: str) -> UnitFeature:
    where = f"{name} feature #{i}"
    if not isinstance(feat, dict) or feat.get("type") != "Feature":
        raise ValidationError(f"{where} is not a GeoJSON Feature", code="corrupt_vector")
    props = feat.get("properties") or {}
    if not isinstance(props, dict):
        raise ValidationError(f"{where} properties must be an object", code="corrupt_vector")

    raw_id = props.get(spec.id_property)
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or str(raw_id).strip() == "":
        raise ValidationError(f"{where} lacks a string/integer '{spec.id_property}' property",
                              code="missing_unit_id")
    unit_id = spec.unit_id_prefix + str(raw_id).strip()
    if unit_id in seen:
        raise ValidationError(f"{where} duplicates unit_id {unit_id!r}", code="duplicate_unit_id")
    seen.add(unit_id)
    unit_name = props.get(spec.name_property)
    if not isinstance(unit_name, str) or not unit_name.strip():
        raise ValidationError(f"{where} ({unit_id}) lacks a non-empty '{spec.name_property}' property",
                              code="missing_name")
    level = props.get(spec.level_property)
    level = None if level is None else str(level)

    gj = feat.get("geometry")
    if not isinstance(gj, dict) or gj.get("type") not in ("Polygon", "MultiPolygon"):
        gtype = gj.get("type") if isinstance(gj, dict) else None
        raise ValidationError(f"{where} ({unit_id}) geometry type {gtype!r} is not Polygon/MultiPolygon",
                              code="unsupported_geometry")
    try:
        geom = shapely.force_2d(shape(gj))
    except Exception as exc:  # shapely raises several types for malformed coordinates
        raise ValidationError(f"{where} ({unit_id}) has malformed coordinates: {exc}",
                              code="invalid_geometry") from exc
    _check_geom(geom, where, unit_id)
    if transformer is not None:
        geom = shapely.transform(geom, lambda c: np.column_stack(transformer.transform(c[:, 0], c[:, 1])))
        _check_geom(geom, where + " (after reprojection)", unit_id)
    minx, miny, maxx, maxy = geom.bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise ValidationError(f"{where} ({unit_id}) lies outside lon/lat range; wrong or missing CRS?",
                              code="invalid_coordinates")

    polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
    multi = MultiPolygon([orient(p, sign=1.0) for p in polys])
    clean_props = {k: v for k, v in props.items() if _json_safe(v)}
    return UnitFeature(unit_id=unit_id, name=unit_name.strip(), level=level, geom=multi, properties=clean_props)


def _check_geom(geom, where: str, unit_id: str) -> None:
    if geom.is_empty:
        raise ValidationError(f"{where} ({unit_id}) geometry is empty", code="invalid_geometry")
    coords = shapely.get_coordinates(geom)
    if not np.isfinite(coords).all():
        raise ValidationError(f"{where} ({unit_id}) has non-finite coordinates", code="invalid_geometry")
    if not geom.is_valid:
        raise ValidationError(f"{where} ({unit_id}) geometry is invalid: {explain_validity(geom)}",
                              code="invalid_geometry")
    if geom.area <= 0:
        raise ValidationError(f"{where} ({unit_id}) polygon has zero area", code="invalid_geometry")


def _json_safe(v: Any) -> bool:
    try:
        json.dumps(v, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return not (isinstance(v, float) and not math.isfinite(v))


def parse_geojson_bytes(data: bytes) -> list[dict[str, Any]]:
    """Read back a normalized asset (used for post-write verification)."""
    return json.loads(data.decode("utf-8"))["features"]
