# Read-only access to catalog.current_datasets (ready rasters) for the tile service. Used by main.
# The pygeoapi image has its own catalog.py: separate image, separate DB driver. The optional
# catalog.render_profile_for (migration core/0008) is probed at run time, so older schemas still work.

from __future__ import annotations

import logging
import os
import time

import psycopg

from .assets import CatalogUnavailable, RasterRecord

LOGGER = logging.getLogger(__name__)

_COLUMNS = """
    c.dataset_id, c.version, c.title, c.asset_path, c.crs, c.nodata, c.units, c.year,
    ST_XMin(c.bbox), ST_YMin(c.bbox), ST_XMax(c.bbox), ST_YMax(c.bbox),
    c.product_level, c.resolution_x, c.resolution_y,
    c.provenance -> 'group_id', c.provenance -> 'synthetic', c.provenance -> 'license',
    c.provenance -> 'citation'
"""

_RENDER_COLUMNS = """,
    r.profile_id, r.units, r.colormap, r.rescale_min, r.rescale_max, r.title, r.description
"""

_FROM = "FROM catalog.current_datasets c"
_RENDER_JOIN = " LEFT JOIN LATERAL catalog.render_profile_for(c.dataset_id, c.version) r ON true"
_LOOKUP_WHERE = " WHERE c.dataset_id = %s AND c.kind = 'raster' AND c.status = 'ready'"
_LIST_WHERE = " WHERE c.kind = 'raster' AND c.status = 'ready' ORDER BY c.dataset_id"

_HAS_RENDER_SQL = ("SELECT CASE WHEN to_regprocedure('catalog.render_profile_for(text,integer)') IS NOT NULL "
                   "THEN has_function_privilege('catalog.render_profile_for(text,integer)', 'EXECUTE') "
                   "ELSE false END")
_RENDER_PROBE_TTL = 60.0  # seconds between capability probes


# libpq connection string from the POSTGRES_* env vars. Used by main.
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


# Non-blank string or None (provenance values are untrusted JSON). Used by _row_to_record.
def _text(value):
    return value if isinstance(value, str) and value.strip() else None


# Converts a _COLUMNS (+ _RENDER_COLUMNS when with_render) row into a RasterRecord.
# Used by Catalog.lookup_raster/list_rasters and the unit tests.
def _row_to_record(row, with_render: bool) -> RasterRecord:
    bbox = None if row[8] is None else (row[8], row[9], row[10], row[11])
    resolution = None
    if row[13] is not None and row[14] is not None:
        resolution = (abs(float(row[13])), abs(float(row[14])))
    public = {
        "group_id": _text(row[15]),
        "synthetic": row[16] if isinstance(row[16], bool) else None,
        "license": _text(row[17]),
        "citation": _text(row[18]),
    }
    render = None
    if with_render and row[19] is not None:
        render = {
            "profile_id": row[19], "units": row[20], "colormap": row[21],
            "rescale": [float(row[22]), float(row[23])], "title": row[24], "description": row[25],
        }
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
        product_level=row[12],
        resolution=resolution,
        public=public,
        render=render,
    )


# Catalog queries over a fresh read-only connection per call. Used by main.
class Catalog:
    # conninfo comes from conninfo_from_env.
    def __init__(self, conninfo: str):
        self._conninfo = conninfo
        self._render_checked_at = 0.0
        self._has_render = False

    # Runs one query; any DB error becomes CatalogUnavailable (HTTP 503).
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

    # Whether catalog.render_profile_for exists and is executable (re-probed every _RENDER_PROBE_TTL s).
    # Used by _select.
    def _render_available(self) -> bool:
        now = time.monotonic()
        if now - self._render_checked_at > _RENDER_PROBE_TTL:
            self._has_render = bool(self._query(_HAS_RENDER_SQL)[0][0])
            self._render_checked_at = now
        return self._has_render

    # Runs the raster SELECT (joined to the render profile when available); returns (rows, with_render).
    # Used by lookup_raster and list_rasters.
    def _select(self, where: str, params: tuple) -> tuple[list, bool]:
        with_render = self._render_available()
        sql = ("SELECT " + _COLUMNS + (_RENDER_COLUMNS if with_render else "") + " " + _FROM
               + (_RENDER_JOIN if with_render else "") + where)
        return self._query(sql, params), with_render

    # The ready raster with this id, or None. Used as AssetResolver's lookup.
    def lookup_raster(self, dataset_id: str) -> RasterRecord | None:
        rows, with_render = self._select(_LOOKUP_WHERE, (dataset_id,))
        return _row_to_record(rows[0], with_render) if rows else None

    # All ready rasters. Used by main.list_datasets.
    def list_rasters(self) -> list[RasterRecord]:
        rows, with_render = self._select(_LIST_WHERE, ())
        return [_row_to_record(r, with_render) for r in rows]

    # Raises CatalogUnavailable unless the catalog view is queryable. Used by main.readyz.
    def ping(self) -> bool:
        self._query("SELECT 1 FROM catalog.current_datasets LIMIT 1")
        return True
