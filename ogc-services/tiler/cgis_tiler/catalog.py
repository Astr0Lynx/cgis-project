"""Read-only access to ``catalog.current_datasets`` (frozen v1 contract)."""

from __future__ import annotations

import logging
import os

import psycopg

from .assets import CatalogUnavailable, RasterRecord

LOGGER = logging.getLogger(__name__)

_COLUMNS = """
    dataset_id, version, title, asset_path, crs, nodata, units, year,
    ST_XMin(bbox), ST_YMin(bbox), ST_XMax(bbox), ST_YMax(bbox)
"""

_LOOKUP_SQL = f"""
SELECT {_COLUMNS}
FROM catalog.current_datasets
WHERE dataset_id = %s AND kind = 'raster' AND status = 'ready'
"""

_LIST_SQL = f"""
SELECT {_COLUMNS}
FROM catalog.current_datasets
WHERE kind = 'raster' AND status = 'ready'
ORDER BY dataset_id
"""


def conninfo_from_env(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return psycopg.conninfo.make_conninfo(
        host=env.get("POSTGRES_HOST", "db"),
        port=env.get("POSTGRES_PORT", "5432"),
        dbname=env.get("POSTGRES_DB", "cgis"),
        user=env.get("POSTGRES_USER", "cgis"),
        password=env.get("POSTGRES_PASSWORD", ""),
        connect_timeout=5,
        application_name="cgis-tiles",
    )


def _row_to_record(row) -> RasterRecord:
    bbox = None if row[8] is None else (row[8], row[9], row[10], row[11])
    return RasterRecord(
        dataset_id=row[0],
        version=row[1],
        title=row[2],
        asset_path=row[3],
        crs=row[4],
        nodata=None if row[5] is None else float(row[5]),
        units=row[6],
        year=row[7],
        bbox=bbox,
    )


class Catalog:
    def __init__(self, conninfo: str):
        self._conninfo = conninfo

    def _query(self, sql: str, params: tuple = ()) -> list:
        try:
            with psycopg.connect(self._conninfo) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
        except psycopg.Error as err:
            LOGGER.error("catalog query failed: %s", err.__class__.__name__)
            raise CatalogUnavailable("Dataset catalog is unavailable") from err

    def lookup_raster(self, dataset_id: str) -> RasterRecord | None:
        rows = self._query(_LOOKUP_SQL, (dataset_id,))
        return _row_to_record(rows[0]) if rows else None

    def list_rasters(self) -> list[RasterRecord]:
        return [_row_to_record(r) for r in self._query(_LIST_SQL)]

    def ping(self) -> bool:
        self._query("SELECT 1 FROM catalog.current_datasets LIMIT 1")
        return True
