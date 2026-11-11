# Thin Python wrappers around the SQL primitives of migrations/core (catalog, geo, ops).
# All values are bound parameters; geometries go in as GeoJSON and come back as GeoJSON dicts.

from __future__ import annotations

import json
from typing import Any, Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DATASET_COLUMNS = (
    "dataset_id, version, title, description, kind, product_level, year, crs, "
    "ST_AsGeoJSON(bbox)::json AS bbox, resolution_x, resolution_y, units, nodata, "
    "asset_path, checksum_sha256, source, provenance, status, created_at, updated_at"
)
UNIT_COLUMNS = (
    "unit_id, name, level, ST_AsGeoJSON(geom)::json AS geom, properties, "
    "dataset_id, dataset_version, source, provenance"
)


class CatalogConflict(Exception):
    pass


# Runs a query and returns all rows as dicts. Used by the list-returning helpers below.
def _fetch_all(conn: psycopg.Connection, query: str, params) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, params).fetchall()


# Runs a query and returns the first row as a dict (or None). Used by resolve_dataset,
# register_dataset and get_job.
def _fetch_one(conn: psycopg.Connection, query: str, params) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, params).fetchone()


# SQL fragment + params for a GeoJSON geometry tagged with the SRID of `crs` ("EPSG:n").
# Used by units_intersecting and unit_overlap.
def _geom_param(geometry: dict | str, crs: str) -> tuple[str, list[Any]]:
    text = json.dumps(geometry) if isinstance(geometry, dict) else geometry
    return "ST_SetSRID(ST_GeomFromGeoJSON(%s), cgis.srid_from_crs(%s))", [text, crs]


# Validates (xmin, ymin, xmax, ymax) and appends the SRID. Used by datasets_in_bbox and units_in_bbox.
def _bbox_params(bbox: Sequence[float], srid: int) -> list[Any]:
    if len(bbox) != 4:
        raise ValueError("bbox must be (xmin, ymin, xmax, ymax)")
    return [float(v) for v in bbox] + [int(srid)]


# Current ready version of a dataset id, or None. Used by tests/test_catalog.py and tests/test_geo.py.
def resolve_dataset(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    return _fetch_one(conn, f"SELECT {DATASET_COLUMNS} FROM catalog.resolve_dataset(%s)", (dataset_id,))


# Current datasets whose bbox intersects the given bbox (any CRS). Used by tests/test_catalog.py.
def datasets_in_bbox(conn: psycopg.Connection, bbox: Sequence[float], srid: int = 4326,
                     kind: str | None = None) -> list[dict]:
    return _fetch_all(conn, f"SELECT {DATASET_COLUMNS} FROM catalog.datasets_in_bbox(%s, %s, %s, %s, %s, %s)",
                      _bbox_params(bbox, srid) + [kind])


# Units intersecting the given bbox (any CRS). Used by tests/test_geo.py.
def units_in_bbox(conn: psycopg.Connection, bbox: Sequence[float], srid: int = 4326,
                  level: str | None = None) -> list[dict]:
    return _fetch_all(conn, f"SELECT {UNIT_COLUMNS} FROM geo.units_in_bbox(%s, %s, %s, %s, %s, %s)",
                      _bbox_params(bbox, srid) + [level])


# Units intersecting a GeoJSON geometry in `crs`. Used by tests/test_geo.py.
def units_intersecting(conn: psycopg.Connection, geometry: dict | str, crs: str = "EPSG:4326",
                       level: str | None = None) -> list[dict]:
    frag, params = _geom_param(geometry, crs)
    return _fetch_all(conn, f"SELECT {UNIT_COLUMNS} FROM geo.units_intersecting({frag}, %s)", params + [level])


# Per-unit geodesic overlap with a GeoJSON AOI in `crs`. Used by tests/test_geo.py.
def unit_overlap(conn: psycopg.Connection, geometry: dict | str, crs: str = "EPSG:4326",
                 level: str | None = None) -> list[dict]:
    frag, params = _geom_param(geometry, crs)
    return _fetch_all(conn, f"SELECT * FROM geo.unit_overlap({frag}, %s)", params + [level])


# Units of one dataset version (default: its current ready version), ordered by unit_id.
# Used by tests/test_versioned_units.py.
def dataset_units(conn: psycopg.Connection, dataset_id: str, version: int | None = None) -> list[dict]:
    return _fetch_all(conn, f"SELECT {UNIT_COLUMNS} FROM geo.dataset_units(%s, %s)", (dataset_id, version))


# Every stored version of a unit within a dataset, newest first, with catalog status.
# Used by tests/test_versioned_units.py.
def unit_versions(conn: psycopg.Connection, dataset_id: str, unit_id: str) -> list[dict]:
    return _fetch_all(conn, "SELECT dataset_id, dataset_version, unit_id, name, level, catalog_status, is_current,"
                            " ST_AsGeoJSON(geom)::json AS geom FROM geo.unit_versions(%s, %s)",
                      (dataset_id, unit_id))


# Shared display scale for a raster dataset version, or None (per-dataset autoscale).
# Used by tests/test_versioned_units.py.
def render_profile_for(conn: psycopg.Connection, dataset_id: str, version: int | None = None) -> dict | None:
    return _fetch_one(conn, "SELECT * FROM catalog.render_profile_for(%s, %s)", (dataset_id, version))


# Idempotent catalog.register_dataset; a conflicting registration raises CatalogConflict.
# Used by fixtures.load_units and tests/test_catalog.py.
def register_dataset(conn: psycopg.Connection, record: dict) -> dict:
    try:
        return _fetch_one(conn, "SELECT * FROM catalog.register_dataset(%s)", (Jsonb(record),))
    except psycopg.errors.UniqueViolation as exc:
        raise CatalogConflict(str(exc).strip()) from exc


# catalog.set_dataset_status. Used by fixtures.load_units and tests/test_catalog.py.
def set_dataset_status(conn: psycopg.Connection, dataset_id: str, version: int, status: str) -> None:
    conn.execute("SELECT catalog.set_dataset_status(%s, %s, %s)", (dataset_id, version, status))


# ops.create_job; returns the new job_id. Used by tests/test_jobs.py.
def create_job(conn: psycopg.Connection, kind: str, input: dict | None = None) -> UUID:
    return conn.execute("SELECT job_id FROM ops.create_job(%s, %s)", (kind, Jsonb(input or {}))).fetchone()[0]


# ops.start_job (queued -> running). Used by tests/test_jobs.py.
def start_job(conn: psycopg.Connection, job_id: UUID) -> None:
    conn.execute("SELECT ops.start_job(%s)", (job_id,))


# ops.complete_job with a result file path and/or inline JSON result. Used by tests/test_jobs.py.
def complete_job(conn: psycopg.Connection, job_id: UUID, result_path: str | None = None,
                 result: dict | None = None) -> None:
    conn.execute("SELECT ops.complete_job(%s, %s, %s)",
                 (job_id, result_path, Jsonb(result) if result is not None else None))


# ops.fail_job with a user-presentable error. Used by tests/test_jobs.py.
def fail_job(conn: psycopg.Connection, job_id: UUID, error: str) -> None:
    conn.execute("SELECT ops.fail_job(%s, %s)", (job_id, error))


# Full ops.jobs row as a dict, or None. Used by tests/test_jobs.py.
def get_job(conn: psycopg.Connection, job_id: UUID) -> dict | None:
    return _fetch_one(conn, "SELECT * FROM ops.jobs WHERE job_id = %s", (job_id,))
