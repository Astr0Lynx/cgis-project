"""Read ready datasets from ``catalog.current_datasets`` (frozen v1 contract)."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, replace

LOGGER = logging.getLogger(__name__)

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

# Optional surfaces, detected at run time so the publisher keeps working on older schemas
# (and on the OGC test stack's stub catalog):
#   catalog.render_profile_for()  database core/0008: shared display scales (P3)
#   cgis_app.dataset_access       application-api (Agent 5): per-dataset access level
CAPABILITIES_SQL = """
SELECT to_regprocedure('catalog.render_profile_for(text,integer)') IS NOT NULL
         AND has_function_privilege('catalog.render_profile_for(text,integer)', 'EXECUTE'),
       to_regclass('cgis_app.dataset_access') IS NOT NULL
"""

RENDER_SQL = """
SELECT c.dataset_id, r.profile_id, r.units, r.colormap, r.rescale_min, r.rescale_max,
       r.title, r.description, r.legend
FROM catalog.current_datasets c
CROSS JOIN LATERAL catalog.render_profile_for(c.dataset_id, c.version) r
WHERE c.kind = 'raster' AND c.status = 'ready'
"""

RESTRICTED_SQL = "SELECT dataset_id FROM cgis_app.dataset_access WHERE access = 'admins'"


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
    # Shared display scale (catalog.render_profiles, database core/0008) or None.
    render: dict | None = None


def db_params_from_env(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "host": env.get("POSTGRES_HOST", "db"),
        "port": int(env.get("POSTGRES_PORT", "5432")),
        "dbname": env.get("POSTGRES_DB", "cgis"),
        "user": env.get("POSTGRES_USER", "cgis"),
        "password": env.get("POSTGRES_PASSWORD", ""),
    }


def _num(value):
    return None if value is None else float(value)


def _row_to_dataset(row) -> CatalogDataset:
    provenance = row[18]
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except ValueError:
            provenance = {}
    bbox = None if row[8] is None else (float(row[8]), float(row[9]), float(row[10]), float(row[11]))
    return CatalogDataset(
        dataset_id=row[0], version=row[1], title=row[2], description=row[3],
        kind=row[4], product_level=row[5], year=row[6], crs=row[7], bbox=bbox,
        resolution_x=_num(row[12]), resolution_y=_num(row[13]), units=row[14],
        nodata=_num(row[15]), asset_path=row[16], source=row[17],
        provenance=provenance if isinstance(provenance, dict) else {},
        status=row[19],
    )


class CatalogReader:
    def __init__(self, params: dict, wait_seconds: float = 0.0):
        self._params = params
        self._wait = wait_seconds

    def _connect(self):
        import psycopg2

        deadline = time.monotonic() + self._wait
        while True:
            try:
                conn = psycopg2.connect(connect_timeout=5, application_name="cgis-ogc-publisher", **self._params)
                conn.set_session(readonly=True, autocommit=True)
                return conn
            except psycopg2.OperationalError as err:
                if time.monotonic() >= deadline:
                    raise
                LOGGER.info("database not reachable yet (%s); retrying", err.__class__.__name__)
                time.sleep(2)

    def read(self, vector_schema: str, vector_table: str) -> tuple[list[CatalogDataset], list[str]]:
        """Return ready, publishable datasets and the column names of the vector table.

        Datasets marked ``admins``-only in ``cgis_app.dataset_access`` are not published (Agent 5
        CR R5). Raises ``RestrictedListUnavailable`` if that table exists but cannot be read, so a
        refresh keeps the previous configuration instead of publishing restricted datasets.
        """
        import psycopg2

        conn = self._connect()
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
                            "SELECT on it to cgis_reader (see infra/COMPLETION_HANDOFF_REPORT.md)") from err
        finally:
            conn.close()
        if restricted:
            LOGGER.info("not publishing %d admins-only dataset(s)", len(restricted & {d.dataset_id for d in datasets}))
        datasets = [replace(d, render=renders.get(d.dataset_id)) for d in datasets
                    if d.dataset_id not in restricted]
        return datasets, columns


class RestrictedListUnavailable(RuntimeError):
    """The per-dataset access table exists but this role cannot read it (fail closed)."""


def _row_to_render(row) -> dict:
    legend = row[8]
    if isinstance(legend, str):
        try:
            legend = json.loads(legend)
        except ValueError:
            legend = {}
    return {
        "profile_id": row[1], "units": row[2], "colormap": row[3],
        "rescale": [float(row[4]), float(row[5])], "title": row[6], "description": row[7],
        "legend": legend if isinstance(legend, dict) else {},
    }
