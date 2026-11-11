# PostGIS catalog access for the v1 contract: catalog.datasets, catalog.current_datasets, geo.admin_units.
# Optional geo.admin_units columns (dataset_id, dataset_version, properties, source) are filled when present.
# Connection: --dsn > $INGESTION_DATABASE_URL > $DATABASE_URL > libpq PG* variables.

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
import shapely
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import CatalogError, ConflictError
from .manifest import DatasetSpec

log = logging.getLogger(__name__)

OPTIONAL_UNIT_COLUMNS = ("dataset_id", "dataset_version", "properties", "source")
REQUIRED_COLUMNS = {
    ("catalog", "datasets"): {"dataset_id", "version", "title", "description", "kind", "product_level", "year",
                              "crs", "bbox", "resolution_x", "resolution_y", "units", "nodata", "asset_path",
                              "checksum_sha256", "source", "provenance", "status", "created_at", "updated_at"},
    ("catalog", "current_datasets"): {"dataset_id", "version", "status"},
    ("geo", "admin_units"): {"unit_id", "name", "level", "geom"},
}

# INSERT of a full catalog row; callers fill {status} with a SQL literal and append the ON CONFLICT action.
_INSERT_ROW = """
    INSERT INTO catalog.datasets (dataset_id, version, title, description, kind, product_level, year, crs,
        bbox, resolution_x, resolution_y, units, nodata, asset_path, checksum_sha256, source, provenance,
        status, created_at, updated_at)
    VALUES (%(dataset_id)s, %(version)s, %(title)s, %(description)s, %(kind)s, %(product_level)s, %(year)s,
        %(crs)s, ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326), %(resolution_x)s, %(resolution_y)s,
        %(units)s, %(nodata)s, %(asset_path)s, %(checksum)s, %(source)s, %(provenance)s::jsonb,
        {status}, now(), now())
    ON CONFLICT (dataset_id, version) """


# Hide passwords in URL and key=value DSNs. Used by connect for its error message.
def redact(dsn: str) -> str:
    dsn = re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", dsn)
    return re.sub(r"(password\s*=\s*)(\S+)", r"\1***", dsn, flags=re.I)


# Open an autocommit dict-row connection, retrying once a second for up to `wait_seconds`;
# raises CatalogError(db_unavailable). Used by cli._run_manifest and tests.
def connect(dsn: str | None = None, wait_seconds: float = 0.0) -> psycopg.Connection:
    conninfo = dsn or os.environ.get("INGESTION_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            return psycopg.connect(conninfo, autocommit=True, row_factory=dict_row,
                                   application_name="cgis-ingestion")
        except psycopg.OperationalError as exc:
            if time.monotonic() >= deadline:
                first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else exc
                raise CatalogError(f"cannot connect to database ({redact(conninfo) or 'libpq defaults'}): "
                                   f"{first_line}", code="db_unavailable") from exc
            time.sleep(1.0)


# Catalog reads/writes on one autocommit connection. Used by importer.Importer.
class Catalog:
    # Wrap a connection and verify the schema up front. Used by cli._run_manifest and tests.
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.check_schema()

    # Column names of a table or view. Used by check_schema.
    def _columns(self, schema: str, table: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            (schema, table)).fetchall()
        return {r["column_name"] for r in rows}

    # Fail with schema_incompatible if contract columns are missing; remember optional unit columns and
    # whether units are version-aware (`versioned_units`, read by tests). Used by __init__.
    def check_schema(self) -> None:
        for (schema, table), cols in REQUIRED_COLUMNS.items():
            missing = cols - self._columns(schema, table)
            if missing:
                raise CatalogError(f"{schema}.{table} is missing columns {sorted(missing)}; "
                                   "run the database migrations first", code="schema_incompatible")
        self._unit_columns = self._columns("geo", "admin_units")
        # Migration core/0007 keys units by (dataset_id, dataset_version, unit_id); older or contract
        # stand-in schemas keep the global unit_id primary key.
        row = self.conn.execute(
            "SELECT 1 AS ok FROM pg_constraint WHERE conrelid = 'geo.admin_units'::regclass "
            "AND conname = 'admin_units_identity'").fetchone()
        self.versioned_units = row is not None and {"dataset_id", "dataset_version"} <= self._unit_columns

    # Session advisory lock on (dataset_id, version); ConflictError(import_in_progress) if held elsewhere.
    # Used by importer.Importer.import_dataset and tests.
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
            except psycopg.Error:  # connection already broken; the lock dies with the session
                pass

    # Existing row as {status, checksum_sha256, asset_path, provenance} or None.
    # Used by importer.Importer.import_dataset.
    def get(self, spec: DatasetSpec) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT status::text AS status, checksum_sha256, asset_path, provenance "
            "FROM catalog.datasets WHERE dataset_id=%s AND version=%s", (spec.dataset_id, spec.version)).fetchone()
        if row is not None and not isinstance(row["provenance"], dict):
            row["provenance"] = {}
        return row

    # Insert or refresh the row as `staged`; never touches a `ready` row.
    # Used by importer.Importer.import_dataset.
    def stage(self, spec: DatasetSpec, meta: dict[str, Any]) -> None:
        row = self.conn.execute(_INSERT_ROW.format(status="'staged'") + """DO UPDATE SET
                title=EXCLUDED.title, description=EXCLUDED.description, kind=EXCLUDED.kind,
                product_level=EXCLUDED.product_level, year=EXCLUDED.year, crs=EXCLUDED.crs, bbox=EXCLUDED.bbox,
                resolution_x=EXCLUDED.resolution_x, resolution_y=EXCLUDED.resolution_y, units=EXCLUDED.units,
                nodata=EXCLUDED.nodata, asset_path=EXCLUDED.asset_path, checksum_sha256=EXCLUDED.checksum_sha256,
                source=EXCLUDED.source, provenance=EXCLUDED.provenance, status='staged', updated_at=now()
            WHERE catalog.datasets.status::text <> 'ready'
            RETURNING status::text AS status""", self._row_params(spec, meta)).fetchone()
        if row is None:
            raise ConflictError(f"{spec.key} became ready concurrently; refusing to overwrite",
                                code="checksum_conflict")

    # In ONE transaction: upsert vector units (if any) and flip the row staged -> ready.
    # Used by importer.Importer._materialize_and_publish.
    def publish(self, spec: DatasetSpec, checksum: str, asset_path: str, provenance: dict[str, Any],
                units: list[Any] | None = None) -> None:
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

    # Best effort: mark a non-ready row `failed`, or insert a `failed` row when validation produced
    # metadata (`meta`). Returns whether a row was recorded. Used by importer.Importer._record_failure.
    def record_failure(self, spec: DatasetSpec, meta: dict[str, Any] | None, provenance: dict[str, Any]) -> bool:
        try:
            cur = self.conn.execute(
                "UPDATE catalog.datasets SET status='failed', provenance=%s::jsonb, updated_at=now() "
                "WHERE dataset_id=%s AND version=%s AND status::text <> 'ready'",
                (Jsonb(provenance), spec.dataset_id, spec.version))
            if cur.rowcount == 1:
                return True
        except psycopg.Error as exc:
            log.warning("could not record failure for %s: %s", spec.key, exc)
        if meta is None:
            return False
        params = {**self._row_params(spec, meta), "provenance": Jsonb(provenance), "asset_path": None}
        try:
            with self.conn.transaction():
                sql = _INSERT_ROW.format(status="'failed'") + "DO NOTHING"
                return self.conn.execute(sql, params).rowcount == 1
        except psycopg.Error as exc:
            log.warning("could not insert failed record for %s: %s", spec.key, exc)
            return False

    # Query parameters for _INSERT_ROW from the spec and file-derived metadata. Used by stage, record_failure.
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

    # Upsert geo.admin_units rows (filling optional columns that exist). Version-aware schemas write only
    # this (dataset_id, version)'s rows, so other versions/datasets reusing the same ids are untouched; the
    # legacy global unit_id key raises unit_id_conflict if a unit belongs to another dataset.
    # Used by publish (inside its transaction).
    def _upsert_units(self, spec: DatasetSpec, units: list[Any]) -> None:
        extra = [c for c in OPTIONAL_UNIT_COLUMNS if c in self._unit_columns]
        if not self.versioned_units and "dataset_id" in extra:
            clash = self.conn.execute(
                "SELECT unit_id, dataset_id FROM geo.admin_units WHERE unit_id = ANY(%s) "
                "AND dataset_id IS NOT NULL AND dataset_id <> %s",
                ([u.unit_id for u in units], spec.dataset_id)).fetchall()
            if clash:
                raise ConflictError(f"unit_id(s) already owned by another dataset: "
                                    f"{[(r['unit_id'], r['dataset_id']) for r in clash][:5]}",
                                    code="unit_id_conflict")
        names = ["unit_id", "name", "level", "geom", *extra]
        placeholders = ["%s", "%s", "%s", "ST_Multi(ST_GeomFromWKB(%s, 4326))"] + [
            "%s::jsonb" if c == "properties" else "%s" for c in extra]
        if self.versioned_units:
            key_cols, conflict = ("unit_id", "dataset_id", "dataset_version"), "ON CONSTRAINT admin_units_identity"
        else:
            key_cols, conflict = ("unit_id",), "(unit_id)"
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in names if c not in key_cols)
        sql = (f"INSERT INTO geo.admin_units ({', '.join(names)}) VALUES ({', '.join(placeholders)}) "
               f"ON CONFLICT {conflict} DO UPDATE SET {updates}")
        rows = []
        for u in units:
            optional = {"dataset_id": spec.dataset_id, "dataset_version": spec.version,
                        "properties": Jsonb(u.properties), "source": spec.source}
            rows.append([u.unit_id, u.name, u.level, shapely.to_wkb(u.geom, output_dimension=2),
                         *(optional[c] for c in extra)])
        with self.conn.cursor() as cur:
            cur.executemany(sql, rows)
