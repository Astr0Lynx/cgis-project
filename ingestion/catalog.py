"""PostGIS catalog access against the frozen v1 contract (``catalog.datasets``, ``geo.admin_units``).

Only columns named in ``contracts/SHARED_CONTRACTS.md`` are required. Optional ``geo.admin_units``
columns proposed in ``ingestion/docs/contract-change-request.md`` (``dataset_id``,
``dataset_version``, ``properties``, ``source``) are filled when Agent 2's schema provides them.

Connection: ``--dsn`` > ``$INGESTION_DATABASE_URL`` > ``$DATABASE_URL`` > libpq ``PG*`` variables.
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import psycopg
import shapely
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import CatalogError, ConflictError
from .manifest import DatasetSpec

log = logging.getLogger(__name__)

OPTIONAL_UNIT_COLUMNS = ("dataset_id", "dataset_version", "properties", "source")


def resolve_dsn(explicit: str | None = None) -> str:
    return explicit or os.environ.get("INGESTION_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def redact(dsn: str) -> str:
    """Hide passwords in both URL and key=value DSN forms."""
    dsn = re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", dsn)
    return re.sub(r"(password\s*=\s*)(\S+)", r"\1***", dsn, flags=re.I)


def connect(dsn: str | None = None, wait_seconds: float = 0.0) -> psycopg.Connection:
    """Open an autocommit connection; optionally retry until ``wait_seconds`` elapse."""
    conninfo = resolve_dsn(dsn)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            return psycopg.connect(conninfo, autocommit=True, row_factory=dict_row,
                                   application_name="cgis-ingestion")
        except psycopg.OperationalError as exc:
            if time.monotonic() >= deadline:
                raise CatalogError(f"cannot connect to database ({redact(conninfo) or 'libpq defaults'}): "
                                   f"{str(exc).strip().splitlines()[0] if str(exc).strip() else exc}",
                                   code="db_unavailable") from exc
            time.sleep(1.0)


@dataclass
class ExistingRecord:
    status: str
    checksum_sha256: str
    asset_path: str | None
    provenance: dict[str, Any]


class Catalog:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self._unit_columns: set[str] | None = None
        self.check_schema()

    # -- schema ------------------------------------------------------------------------------------
    def _columns(self, schema: str, table: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            (schema, table)).fetchall()
        return {r["column_name"] for r in rows}

    def check_schema(self) -> None:
        required = {
            ("catalog", "datasets"): {"dataset_id", "version", "title", "description", "kind", "product_level",
                                      "year", "crs", "bbox", "resolution_x", "resolution_y", "units", "nodata",
                                      "asset_path", "checksum_sha256", "source", "provenance", "status",
                                      "created_at", "updated_at"},
            ("catalog", "current_datasets"): {"dataset_id", "version", "status"},
            ("geo", "admin_units"): {"unit_id", "name", "level", "geom"},
        }
        for (schema, table), cols in required.items():
            missing = cols - self._columns(schema, table)
            if missing:
                raise CatalogError(f"{schema}.{table} is missing columns {sorted(missing)}; "
                                   "run the database migrations first", code="schema_incompatible")
        self._unit_columns = self._columns("geo", "admin_units")

    # -- locking -----------------------------------------------------------------------------------
    @contextmanager
    def lock(self, spec: DatasetSpec) -> Iterator[None]:
        key = f"catalog.datasets:{spec.dataset_id}:{spec.version}"
        got = self.conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (key,)).fetchone()
        if not got["ok"]:
            raise ConflictError(f"another import of {spec.key} is in progress", code="import_in_progress")
        try:
            yield
        finally:
            try:
                self.conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
            except psycopg.Error:  # connection already broken; lock dies with the session
                pass

    # -- reads -------------------------------------------------------------------------------------
    def get(self, spec: DatasetSpec) -> ExistingRecord | None:
        row = self.conn.execute(
            "SELECT status::text AS status, checksum_sha256, asset_path, provenance "
            "FROM catalog.datasets WHERE dataset_id=%s AND version=%s", (spec.dataset_id, spec.version)).fetchone()
        if row is None:
            return None
        prov = row["provenance"]
        return ExistingRecord(status=row["status"], checksum_sha256=row["checksum_sha256"],
                              asset_path=row["asset_path"], provenance=prov if isinstance(prov, dict) else {})

    # -- writes ------------------------------------------------------------------------------------
    def stage(self, spec: DatasetSpec, meta: dict[str, Any]) -> None:
        """Insert/refresh the row as ``staged``. Never touches a ``ready`` row."""
        params = self._row_params(spec, meta)
        row = self.conn.execute(
            """
            INSERT INTO catalog.datasets (dataset_id, version, title, description, kind, product_level, year, crs,
                bbox, resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance,
                status, created_at, updated_at)
            VALUES (%(dataset_id)s, %(version)s, %(title)s, %(description)s, %(kind)s, %(product_level)s, %(year)s,
                %(crs)s, ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326), %(resolution_x)s, %(resolution_y)s,
                %(units)s, %(nodata)s, %(asset_path)s, %(checksum)s, %(source)s, %(provenance)s::jsonb,
                'staged', now(), now())
            ON CONFLICT (dataset_id, version) DO UPDATE SET
                title=EXCLUDED.title, description=EXCLUDED.description, kind=EXCLUDED.kind,
                product_level=EXCLUDED.product_level, year=EXCLUDED.year, crs=EXCLUDED.crs, bbox=EXCLUDED.bbox,
                resolution_x=EXCLUDED.resolution_x, resolution_y=EXCLUDED.resolution_y, units=EXCLUDED.units,
                nodata=EXCLUDED.nodata, asset_path=EXCLUDED.asset_path, checksum_sha256=EXCLUDED.checksum_sha256,
                source=EXCLUDED.source, provenance=EXCLUDED.provenance, status='staged', updated_at=now()
            WHERE catalog.datasets.status::text <> 'ready'
            RETURNING status::text AS status
            """, params).fetchone()
        if row is None:
            raise ConflictError(f"{spec.key} became ready concurrently; refusing to overwrite",
                                code="checksum_conflict")

    def publish(self, spec: DatasetSpec, checksum: str, asset_path: str, provenance: dict[str, Any],
                units: list[Any] | None = None) -> None:
        """Atomically (one transaction) upsert vector units and flip the row ``staged`` -> ``ready``."""
        with self.conn.transaction():
            if units:
                self._upsert_units(spec, units)
            cur = self.conn.execute(
                "UPDATE catalog.datasets SET status='ready', asset_path=%s, provenance=%s::jsonb, updated_at=now() "
                "WHERE dataset_id=%s AND version=%s AND status::text='staged' AND checksum_sha256=%s",
                (asset_path, Jsonb(provenance), spec.dataset_id, spec.version, checksum))
            if cur.rowcount != 1:
                raise ConflictError(f"{spec.key} is no longer the staged record this import created",
                                    code="stage_lost")

    def mark_failed(self, spec: DatasetSpec, provenance: dict[str, Any]) -> bool:
        """Best effort: flip a non-ready row to ``failed`` with the error in provenance."""
        try:
            cur = self.conn.execute(
                "UPDATE catalog.datasets SET status='failed', provenance=%s::jsonb, updated_at=now() "
                "WHERE dataset_id=%s AND version=%s AND status::text <> 'ready'",
                (Jsonb(provenance), spec.dataset_id, spec.version))
            return cur.rowcount == 1
        except psycopg.Error as exc:
            log.warning("could not record failure for %s: %s", spec.key, exc)
            return False

    def record_failure(self, spec: DatasetSpec, meta: dict[str, Any] | None, provenance: dict[str, Any]) -> bool:
        """Record a failure, inserting a ``failed`` row when validation produced enough metadata."""
        if self.mark_failed(spec, provenance):
            return True
        if meta is None:
            return False
        params = self._row_params(spec, meta)
        params["provenance"] = Jsonb(provenance)
        try:
            with self.conn.transaction():
                cur = self.conn.execute(
                    """
                    INSERT INTO catalog.datasets (dataset_id, version, title, description, kind, product_level,
                        year, crs, bbox, resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256,
                        source, provenance, status, created_at, updated_at)
                    VALUES (%(dataset_id)s, %(version)s, %(title)s, %(description)s, %(kind)s, %(product_level)s,
                        %(year)s, %(crs)s, ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326), %(resolution_x)s,
                        %(resolution_y)s, %(units)s, %(nodata)s, NULL, %(checksum)s, %(source)s,
                        %(provenance)s::jsonb, 'failed', now(), now())
                    ON CONFLICT (dataset_id, version) DO NOTHING
                    """, params)
                return cur.rowcount == 1
        except psycopg.Error as exc:
            log.warning("could not insert failed record for %s: %s", spec.key, exc)
            return False

    # -- helpers -----------------------------------------------------------------------------------
    @staticmethod
    def _row_params(spec: DatasetSpec, meta: dict[str, Any]) -> dict[str, Any]:
        w, s, e, n = meta["bbox_wgs84"]
        return {
            "dataset_id": spec.dataset_id, "version": spec.version, "title": spec.title,
            "description": spec.description, "kind": spec.kind, "product_level": spec.product_level,
            "year": spec.year, "crs": meta["crs"], "w": w, "s": s, "e": e, "n": n,
            "resolution_x": meta.get("resolution_x"), "resolution_y": meta.get("resolution_y"),
            "units": spec.units, "nodata": meta.get("nodata"), "asset_path": meta["asset_path"],
            "checksum": meta["checksum_sha256"], "source": spec.source, "provenance": Jsonb(meta["provenance"]),
        }

    def _upsert_units(self, spec: DatasetSpec, units: list[Any]) -> None:
        cols = self._unit_columns or set()
        extra = [c for c in OPTIONAL_UNIT_COLUMNS if c in cols]
        if "dataset_id" in extra:
            ids = [u.unit_id for u in units]
            clash = self.conn.execute(
                "SELECT unit_id, dataset_id FROM geo.admin_units WHERE unit_id = ANY(%s) "
                "AND dataset_id IS NOT NULL AND dataset_id <> %s", (ids, spec.dataset_id)).fetchall()
            if clash:
                raise ConflictError(f"unit_id(s) already owned by another dataset: "
                                    f"{[(r['unit_id'], r['dataset_id']) for r in clash][:5]}",
                                    code="unit_id_conflict")
        names = ["unit_id", "name", "level", "geom", *extra]
        placeholders = ["%s", "%s", "%s", "ST_Multi(ST_GeomFromWKB(%s, 4326))"] + [
            "%s::jsonb" if c == "properties" else "%s" for c in extra]
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in names if c != "unit_id")
        sql = (f"INSERT INTO geo.admin_units ({', '.join(names)}) VALUES ({', '.join(placeholders)}) "
               f"ON CONFLICT (unit_id) DO UPDATE SET {updates}")
        rows = []
        for u in units:
            vals: list[Any] = [u.unit_id, u.name, u.level, shapely.to_wkb(u.geom, output_dimension=2)]
            for c in extra:
                vals.append({"dataset_id": spec.dataset_id, "dataset_version": spec.version,
                             "properties": Jsonb(u.properties), "source": spec.source}[c])
            rows.append(vals)
        with self.conn.cursor() as cur:
            cur.executemany(sql, rows)
