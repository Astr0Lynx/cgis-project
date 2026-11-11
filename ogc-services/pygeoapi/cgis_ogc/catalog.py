# Reads ready datasets from catalog.current_datasets (read-only) for the publisher.
# The tiler image has its own catalog.py: separate image, separate DB driver.

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace

LOGGER = logging.getLogger(__name__)

# Catalog dataset_ids appear in URLs; anything else is never published or forwarded.
DATASET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

DATASETS_SQL = """
SELECT dataset_id, version, title, description, kind, product_level, year, crs,
       ST_XMin(bbox), ST_YMin(bbox), ST_XMax(bbox), ST_YMax(bbox),
       resolution_x, resolution_y, units, nodata, asset_path, source,
       provenance, status
FROM catalog.current_datasets
WHERE status = 'ready'
ORDER BY dataset_id
"""

COLUMNS_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position
"""

# Optional surfaces, probed at run time so the publisher also works on older schemas:
# catalog.render_profile_for() (shared display scales, migration core/0008) and
# cgis_app.dataset_access (application API per-dataset access). pg_catalog lookups need no USAGE
# on the schema, so a missing grant surfaces as RestrictedListUnavailable instead of an opaque error.
CAPABILITIES_SQL = """
SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n
                           ON n.oid = p.pronamespace
                          WHERE n.nspname = 'catalog' AND p.proname = 'render_profile_for')
            THEN has_function_privilege('catalog.render_profile_for(text,integer)', 'EXECUTE')
            ELSE false END,
       EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'cgis_app' AND c.relname = 'dataset_access')
"""

RENDER_SQL = """
SELECT c.dataset_id, r.profile_id, r.units, r.colormap, r.rescale_min, r.rescale_max,
       r.title, r.description, r.legend
FROM catalog.current_datasets c
CROSS JOIN LATERAL catalog.render_profile_for(c.dataset_id, c.version) r
WHERE c.kind = 'raster' AND c.status = 'ready'
"""

RESTRICTED_SQL = "SELECT dataset_id FROM cgis_app.dataset_access WHERE access = 'admins'"


# One row of catalog.current_datasets. Built by read_catalog; consumed by publish.build_config.
@dataclass(frozen=True)
class CatalogDataset:
    dataset_id: str
    version: int
    title: str
    kind: str
    description: str | None = None
    product_level: str | None = None
    year: int | None = None
    crs: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    resolution_x: float | None = None
    resolution_y: float | None = None
    units: str | None = None
    nodata: float | None = None
    asset_path: str | None = None
    source: str | None = None
    provenance: dict = field(default_factory=dict)
    status: str = "ready"
    render: dict | None = None   # shared display scale from catalog.render_profile_for, or None


# Raised by read_catalog when cgis_app.dataset_access exists but is unreadable (fail closed: the
# refresh fails and the previous configuration stays in place).
class RestrictedListUnavailable(RuntimeError):
    pass


# Parses a JSON column that may arrive as text; anything but an object becomes {}.
# Used by _row_to_dataset and _row_to_render.
def _json_object(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


# Converts a DATASETS_SQL row into a CatalogDataset. Used by read_catalog.
def _row_to_dataset(row) -> CatalogDataset:
    # NUMERIC -> float, keeping NULL.
    def num(value):
        return None if value is None else float(value)

    bbox = None if row[8] is None else (float(row[8]), float(row[9]), float(row[10]), float(row[11]))
    return CatalogDataset(
        dataset_id=row[0], version=row[1], title=row[2], description=row[3],
        kind=row[4], product_level=row[5], year=row[6], crs=row[7], bbox=bbox,
        resolution_x=num(row[12]), resolution_y=num(row[13]), units=row[14],
        nodata=num(row[15]), asset_path=row[16], source=row[17],
        provenance=_json_object(row[18]),
        status=row[19],
    )


# Converts a RENDER_SQL row into the render dict carried by CatalogDataset. Used by read_catalog.
def _row_to_render(row) -> dict:
    return {
        "profile_id": row[1], "units": row[2], "colormap": row[3],
        "rescale": [float(row[4]), float(row[5])], "title": row[6], "description": row[7],
        "legend": _json_object(row[8]),
    }


# Opens a read-only connection from the POSTGRES_* env vars, retrying for up to wait_seconds.
# Used by read_catalog.
def _connect(wait_seconds: float):
    import psycopg2

    params = {
        "host": os.environ.get("POSTGRES_HOST", "db"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "dbname": os.environ.get("POSTGRES_DB", "cgis"),
        "user": os.environ.get("POSTGRES_USER", "cgis"),
        "password": os.environ.get("POSTGRES_PASSWORD", ""),
    }
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            conn = psycopg2.connect(connect_timeout=5, application_name="cgis-ogc-publisher", **params)
            conn.set_session(readonly=True, autocommit=True)
            return conn
        except psycopg2.OperationalError as err:
            if time.monotonic() >= deadline:
                raise
            LOGGER.info("database not reachable yet (%s); retrying", err.__class__.__name__)
            time.sleep(2)


# Returns (ready, publishable datasets with their render profile, column names of the vector table).
# Datasets marked admins-only in cgis_app.dataset_access are left out. Used by publish.main.
def read_catalog(vector_schema: str, vector_table: str, wait_seconds: float = 0.0
                 ) -> tuple[list[CatalogDataset], list[str]]:
    import psycopg2

    conn = _connect(wait_seconds)
    try:
        with conn.cursor() as cur:
            cur.execute(DATASETS_SQL)
            datasets = [_row_to_dataset(r) for r in cur.fetchall()]
            cur.execute(COLUMNS_SQL, (vector_schema, vector_table))
            columns = [r[0] for r in cur.fetchall()]
            cur.execute(CAPABILITIES_SQL)
            has_render, has_access = cur.fetchone()
            renders: dict[str, dict] = {}
            if has_render:
                cur.execute(RENDER_SQL)
                renders = {r[0]: _row_to_render(r) for r in cur.fetchall()}
            restricted: set[str] = set()
            if has_access:
                try:
                    cur.execute(RESTRICTED_SQL)
                    restricted = {r[0] for r in cur.fetchall()}
                except psycopg2.errors.InsufficientPrivilege as err:
                    raise RestrictedListUnavailable(
                        "cgis_app.dataset_access exists but is not readable by the OGC role; grant "
                        "SELECT on it to cgis_reader") from err
    finally:
        conn.close()
    if restricted:
        LOGGER.info("not publishing %d admins-only dataset(s)", len(restricted & {d.dataset_id for d in datasets}))
    datasets = [replace(d, render=renders.get(d.dataset_id)) for d in datasets
                if d.dataset_id not in restricted]
    return datasets, columns
