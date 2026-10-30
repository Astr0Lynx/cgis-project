"""Parameterised Python wrappers around the SQL primitives.

Every value travels as a bound parameter; no caller input is ever interpolated
into SQL text. Geometries cross the boundary as GeoJSON (EPSG:4326 unless a CRS
is given) and come back as GeoJSON dicts.
"""

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
    """A registration conflicts with an existing (dataset_id, version)."""


def _geom_param(geometry: dict | str, crs: str) -> tuple[str, list[Any]]:
    """SQL fragment + params producing a geometry with an explicit SRID."""
    text = json.dumps(geometry) if isinstance(geometry, dict) else geometry
    return "ST_SetSRID(ST_GeomFromGeoJSON(%s), cgis.srid_from_crs(%s))", [text, crs]


def _bbox_params(bbox: Sequence[float], srid: int) -> list[Any]:
    if len(bbox) != 4:
        raise ValueError("bbox must be (xmin, ymin, xmax, ymax)")
    return [float(v) for v in bbox] + [int(srid)]


def current_datasets(conn: psycopg.Connection, kind: str | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {DATASET_COLUMNS} FROM catalog.current_datasets"
            " WHERE (%s::text IS NULL OR kind = %s) ORDER BY dataset_id",
            (kind, kind),
        )
        return cur.fetchall()


def resolve_dataset(conn: psycopg.Connection, dataset_id: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {DATASET_COLUMNS} FROM catalog.resolve_dataset(%s)", (dataset_id,))
        return cur.fetchone()


def datasets_in_bbox(conn: psycopg.Connection, bbox: Sequence[float], srid: int = 4326,
                     kind: str | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {DATASET_COLUMNS} FROM catalog.datasets_in_bbox(%s, %s, %s, %s, %s, %s)",
            _bbox_params(bbox, srid) + [kind],
        )
        return cur.fetchall()


def units_in_bbox(conn: psycopg.Connection, bbox: Sequence[float], srid: int = 4326,
                  level: str | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {UNIT_COLUMNS} FROM geo.units_in_bbox(%s, %s, %s, %s, %s, %s)",
            _bbox_params(bbox, srid) + [level],
        )
        return cur.fetchall()


def units_intersecting(conn: psycopg.Connection, geometry: dict | str, crs: str = "EPSG:4326",
                       level: str | None = None) -> list[dict]:
    frag, params = _geom_param(geometry, crs)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {UNIT_COLUMNS} FROM geo.units_intersecting({frag}, %s)", params + [level])
        return cur.fetchall()


def unit_overlap(conn: psycopg.Connection, geometry: dict | str, crs: str = "EPSG:4326",
                 level: str | None = None) -> list[dict]:
    frag, params = _geom_param(geometry, crs)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM geo.unit_overlap({frag}, %s)", params + [level])
        return cur.fetchall()


def register_dataset(conn: psycopg.Connection, record: dict) -> dict:
    """Idempotently register a catalog record (see catalog.register_dataset)."""
    with conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute("SELECT * FROM catalog.register_dataset(%s)", (Jsonb(record),))
        except psycopg.errors.UniqueViolation as exc:
            raise CatalogConflict(str(exc).strip()) from exc
        return cur.fetchone()


def set_dataset_status(conn: psycopg.Connection, dataset_id: str, version: int, status: str) -> None:
    conn.execute("SELECT catalog.set_dataset_status(%s, %s, %s)", (dataset_id, version, status))


def create_job(conn: psycopg.Connection, kind: str, input: dict | None = None) -> UUID:
    return conn.execute("SELECT job_id FROM ops.create_job(%s, %s)", (kind, Jsonb(input or {}))).fetchone()[0]


def start_job(conn: psycopg.Connection, job_id: UUID) -> None:
    conn.execute("SELECT ops.start_job(%s)", (job_id,))


def complete_job(conn: psycopg.Connection, job_id: UUID, result_path: str | None = None,
                 result: dict | None = None) -> None:
    conn.execute("SELECT ops.complete_job(%s, %s, %s)",
                 (job_id, result_path, Jsonb(result) if result is not None else None))


def fail_job(conn: psycopg.Connection, job_id: UUID, error: str) -> None:
    conn.execute("SELECT ops.fail_job(%s, %s)", (job_id, error))


def get_job(conn: psycopg.Connection, job_id: UUID) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM ops.jobs WHERE job_id = %s", (job_id,))
        return cur.fetchone()
