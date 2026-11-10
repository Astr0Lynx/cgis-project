"""Read-only catalog access, dataset access policy and path-free public metadata.

The catalog (``catalog.datasets`` / ``catalog.current_datasets``) is the only source of dataset identity
and asset locations. Clients only ever send ``dataset_id`` (+ optional version); ``asset_path`` and any
provenance field that could reveal a server path, credential or restricted source link are removed
before metadata leaves the service.
"""

from __future__ import annotations

import re
from typing import Any

import psycopg

from .config import Settings

DATASET_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")

_COLUMNS = (
    "d.dataset_id, d.version, d.title, d.description, d.kind, d.product_level, d.year, d.crs, "
    "ST_XMin(d.bbox) AS w, ST_YMin(d.bbox) AS s, ST_XMax(d.bbox) AS e, ST_YMax(d.bbox) AS n, "
    "d.resolution_x, d.resolution_y, d.units, d.nodata, d.asset_path, d.checksum_sha256, d.source, "
    "d.provenance, d.status, d.created_at, d.updated_at"
)

ACCESS_RANK = {"public": 0, "users": 1, "admins": 2}

# Provenance keys never shown to non-admins (server paths, links that may carry signed tokens or
# require approval, internal error traces) — matched case-insensitively against every nested key.
_SECRET_KEY_RE = re.compile(
    r"(path|file|dir|url|uri|href|link|password|passwd|secret|token|credential|cookie|auth|key|email|"
    r"host|dsn|bucket|signature|error|trace)", re.I)
_ALLOWED_KEYS = {"license", "licence", "citation", "doi", "group_id", "region", "synthetic", "method",
                 "notes", "release", "edition", "version", "year", "years", "units", "grid", "resolution",
                 "crs", "producer", "publisher", "organization", "organisation", "attribution", "title",
                 "description", "selection", "temporal_policy", "interpolation", "resampling",
                 "source_ids", "upstream", "parameters_sha256", "model_commit", "checksum_sha256",
                 "source_sha256", "importer", "importer_version", "gdal_version", "started_at",
                 "completed_at", "uploaded_at", "feature_count", "stored_crs",
                 "geometry_type", "width", "height", "dtype", "nodata", "band", "band_count", "mask",
                 "stats", "valid_count", "nodata_count", "min", "max", "sum", "transform", "native_bounds",
                 "bbox_wgs84", "resolution_x", "resolution_y", "source_units", "source_driver",
                 "source_is_cog", "asset", "format", "layout", "compression", "overviews", "access"}
_ALLOWED_KEYS |= {"author", "authors", "contributors"}
# A string value that looks like a filesystem path or URL is dropped even under an allowed key; the only
# links kept are public DOI resolver links (citations).
_PATHLIKE_RE = re.compile(r"(^/|^[A-Za-z]:\\|^\\\\|://|^~|\.\./|^file:|^vsi|/data/|\\)", re.I)
_DOI_RE = re.compile(r"^https://doi\.org/10\.\d{4,9}/[^\s?#]+$")


def valid_dataset_id(dataset_id: str) -> bool:
    return isinstance(dataset_id, str) and bool(DATASET_ID_RE.match(dataset_id))


def safe_string(value: str) -> str | None:
    if _DOI_RE.match(value):
        return value
    return None if _PATHLIKE_RE.search(value) else value[:2000]


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            if key.lower() not in _ALLOWED_KEYS and _SECRET_KEY_RE.search(key):
                continue
            cleaned = _clean(v, depth + 1)
            if cleaned is not None:
                out[key] = cleaned
        return out
    if isinstance(value, list):
        items = [_clean(v, depth + 1) for v in value[:500]]
        return [v for v in items if v is not None]
    if isinstance(value, str):
        return safe_string(value)
    return value


def public_provenance(provenance: dict[str, Any] | None) -> dict[str, Any]:
    """Provenance subset safe for any reader: no paths, URLs, credentials or error traces."""
    if not isinstance(provenance, dict):
        return {}
    cleaned = _clean(provenance)
    ingestion = cleaned.get("ingestion")
    if isinstance(ingestion, dict):
        ingestion.pop("source_file", None)
    upload = cleaned.get("upload")
    if isinstance(upload, dict):  # who uploaded and the upload id are admin-only information
        for key in ("uploaded_by", "upload_id", "original_filename"):
            upload.pop(key, None)
    return cleaned


_URL_IN_TEXT_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://([^/\s?#@]*@)?([^/\s?#:]+)[^\s]*")


def public_source(source: str | None) -> str | None:
    """The catalog ``source`` text with links reduced to their host and local paths removed.

    Download links for approval-gated sources (e.g. Bhuvan) can carry signed parameters; only the host is
    kept so a reader still sees where the data came from.
    """
    if not source:
        return source
    reduced = _URL_IN_TEXT_RE.sub(lambda m: f"{m.group(2)} (link withheld)", source)
    if re.search(r"(^|\s)(/|~|[A-Za-z]:\\)|\.\./|/data/", reduced):
        return "withheld (contains a server path)"
    return reduced[:500]


def _bbox(row: dict) -> list[float] | None:
    if row.get("w") is None:
        return None
    return [row["w"], row["s"], row["e"], row["n"]]


def _text(prov: dict, *keys: str) -> str | None:
    for key in keys:
        value = prov.get(key)
        if isinstance(value, str):
            return safe_string(value)
    return None


def _license(prov: dict) -> str | None:
    found = _text(prov, "license", "licence")
    if found:
        return found
    src = prov.get("source")
    if isinstance(src, dict):
        return _text(src, "license", "licence")
    return None


def is_synthetic(row: dict) -> bool:
    prov = row.get("provenance") or {}
    text = f"{row.get('source') or ''} {row.get('title') or ''}".lower()
    return bool(prov.get("synthetic")) or "synthetic" in text or "fixture" in text


def public_record(row: dict, *, access: str | None = None, include_provenance: bool = True) -> dict[str, Any]:
    prov = row.get("provenance") or {}
    clean = public_provenance(prov) if include_provenance else {}
    out = {
        "dataset_id": row["dataset_id"],
        "version": row["version"],
        "title": row["title"],
        "description": row.get("description"),
        "kind": row["kind"],
        "product_level": row["product_level"],
        "year": row.get("year"),
        "units": row.get("units"),
        "crs": row.get("crs"),
        "bbox": _bbox(row),
        "resolution_x": row.get("resolution_x"),
        "resolution_y": row.get("resolution_y"),
        "nodata": row.get("nodata"),
        "source": public_source(row.get("source")),
        "license": _license(prov),
        "citation": _text(prov, "citation"),
        "synthetic": is_synthetic(row),
        "group_id": _text(prov, "group_id"),
        "region": _text(prov, "region"),
        "checksum_sha256": row.get("checksum_sha256"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    raster = prov.get("raster") if isinstance(prov.get("raster"), dict) else {}
    if row["kind"] == "raster":
        out["width"] = raster.get("width")
        out["height"] = raster.get("height")
        out["dtype"] = raster.get("dtype")
    if access is not None:
        out["access"] = access
    if include_provenance:
        out["provenance"] = clean
    return out


# ------------------------------------------------------------------ access policy
def access_for(conn: psycopg.Connection, settings: Settings, dataset_ids: list[str]) -> dict[str, str]:
    rows = conn.execute("SELECT dataset_id, access FROM cgis_app.dataset_access WHERE dataset_id = ANY(%s)",
                        (list(dataset_ids),)).fetchall()
    explicit = {r["dataset_id"]: r["access"] for r in rows}
    return {d: explicit.get(d, settings.default_dataset_access) for d in dataset_ids}


def allowed_levels(role: str | None) -> list[str]:
    """Access levels a caller may see: anonymous -> public; user -> public+users; admin -> all."""
    if role == "admin":
        return ["public", "users", "admins"]
    if role == "user":
        return ["public", "users"]
    return ["public"]


def can_read(access: str, role: str | None) -> bool:
    return access in allowed_levels(role)


def set_access(conn: psycopg.Connection, dataset_id: str, access: str, note: str | None, user_id) -> dict:
    return conn.execute(
        "INSERT INTO cgis_app.dataset_access (dataset_id, access, note, updated_by, updated_at) "
        "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (dataset_id) DO UPDATE SET access = EXCLUDED.access, "
        "note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
        (dataset_id, access, note, user_id)).fetchone()


# ------------------------------------------------------------------ queries
def _access_sql(settings: Settings) -> str:
    # Default access applies to datasets without a policy row; bound as a parameter, never formatted.
    return "coalesce(a.access, %(default_access)s)"


def list_current(conn: psycopg.Connection, settings: Settings, *, role: str | None, kind: str | None = None,
                 product_level: str | None = None, year: int | None = None, q: str | None = None,
                 bbox: list[float] | None = None, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    where = [f"{_access_sql(settings)} = ANY(%(levels)s)"]
    params: dict[str, Any] = {"default_access": settings.default_dataset_access, "levels": allowed_levels(role),
                              "limit": limit, "offset": offset}
    if kind:
        where.append("d.kind = %(kind)s")
        params["kind"] = kind
    if product_level:
        where.append("d.product_level = %(product_level)s")
        params["product_level"] = product_level
    if year is not None:
        where.append("d.year = %(year)s")
        params["year"] = year
    if q:
        where.append("(d.dataset_id ILIKE %(q)s OR d.title ILIKE %(q)s)")
        params["q"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if bbox:
        where.append("d.bbox && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)")
        params.update(w=bbox[0], s=bbox[1], e=bbox[2], n=bbox[3])
    clause = " AND ".join(where)
    base = (f"FROM catalog.current_datasets d LEFT JOIN cgis_app.dataset_access a USING (dataset_id) "
            f"WHERE {clause}")
    total = conn.execute(f"SELECT count(*) AS n {base}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {_COLUMNS}, {_access_sql(settings)} AS access {base} "
        "ORDER BY d.product_level, d.year NULLS LAST, d.dataset_id LIMIT %(limit)s OFFSET %(offset)s",
        params).fetchall()
    return rows, total


def ready_versions(conn: psycopg.Connection, dataset_ids: list[str]) -> dict[str, list[dict]]:
    rows = conn.execute("SELECT dataset_id, version, status, created_at FROM catalog.datasets "
                        "WHERE dataset_id = ANY(%s) AND status = 'ready' ORDER BY dataset_id, version DESC",
                        (list(dataset_ids),)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["dataset_id"], []).append(
            {"version": r["version"], "status": r["status"], "created_at": r["created_at"]})
    return out


def get_version(conn: psycopg.Connection, dataset_id: str, version: int | None, *,
                ready_only: bool = True) -> dict | None:
    """One catalog row: the given version, or the current ready version when ``version`` is None."""
    if version is None:
        return conn.execute(f"SELECT {_COLUMNS} FROM catalog.current_datasets d WHERE d.dataset_id = %s",
                            (dataset_id,)).fetchone()
    status_clause = "AND d.status = 'ready'" if ready_only else ""
    return conn.execute(f"SELECT {_COLUMNS} FROM catalog.datasets d WHERE d.dataset_id = %s AND d.version = %s "
                        f"{status_clause}", (dataset_id, version)).fetchone()


def all_versions(conn: psycopg.Connection, dataset_id: str) -> list[dict]:
    return conn.execute(f"SELECT {_COLUMNS} FROM catalog.datasets d WHERE d.dataset_id = %s ORDER BY d.version DESC",
                        (dataset_id,)).fetchall()


def dataset_exists(conn: psycopg.Connection, dataset_id: str) -> bool:
    return conn.execute("SELECT 1 FROM catalog.datasets WHERE dataset_id = %s LIMIT 1", (dataset_id,)).fetchone() \
        is not None
