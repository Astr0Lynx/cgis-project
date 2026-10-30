"""Test/development fixture loader for vector units.

Independent of Agent 3's importer: it only needs a GeoJSON FeatureCollection
whose features carry ``unit_id`` and ``name`` properties. By default it loads
the database test stub (``fixtures/synthetic_towns.stub.geojson``); pass the
generator's ``synthetic_towns.geojson`` to load the authoritative file.

The load is a single transaction and idempotent: the catalog row is registered
(staged), units are upserted, then the row is marked ready. Re-running with the
same file is a no-op; a different file under the same (dataset_id, version)
raises :class:`cgis_db.queries.CatalogConflict`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import queries

STUB_GEOJSON = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_towns.stub.geojson"
_WGS84_CRS_NAMES = {
    "urn:ogc:def:crs:OGC:1.3:CRS84",
    "urn:ogc:def:crs:OGC::CRS84",
    "urn:ogc:def:crs:EPSG::4326",
    "EPSG:4326",
}
_UNIT_KEYS = {"unit_id", "name", "level", "source"}


class FixtureError(ValueError):
    pass


def _parse(raw: bytes) -> list[dict]:
    doc = json.loads(raw)
    if doc.get("type") != "FeatureCollection" or not isinstance(doc.get("features"), list):
        raise FixtureError("expected a GeoJSON FeatureCollection")
    crs_name = (doc.get("crs") or {}).get("properties", {}).get("name")
    if crs_name is not None and crs_name not in _WGS84_CRS_NAMES:
        raise FixtureError(f"unsupported GeoJSON crs {crs_name!r}; RFC 7946 requires WGS84")
    feats = doc["features"]
    if not feats:
        raise FixtureError("FeatureCollection has no features")
    seen = set()
    for i, f in enumerate(feats):
        props = f.get("properties") or {}
        uid, name = props.get("unit_id"), props.get("name")
        if not isinstance(uid, str) or not uid.strip() or not isinstance(name, str) or not name.strip():
            raise FixtureError(f"feature {i} lacks non-empty unit_id/name properties")
        if uid in seen:
            raise FixtureError(f"duplicate unit_id {uid!r}")
        seen.add(uid)
        if not f.get("geometry"):
            raise FixtureError(f"feature {uid!r} has no geometry")
    return feats


def load_units(conn: psycopg.Connection, geojson_path: Path | str | None = None, *,
               dataset_id: str = "synthetic-towns", version: int = 1,
               title: str = "SYNTHETIC towns (deterministic-fixture)",
               source: str = "deterministic-fixture") -> dict:
    path = Path(geojson_path) if geojson_path else STUB_GEOJSON
    raw = path.read_bytes()
    feats = _parse(raw)
    checksum = hashlib.sha256(raw).hexdigest()
    geoms = [json.dumps(f["geometry"]) for f in feats]

    with conn.transaction():
        bbox_ewkt = conn.execute(
            "SELECT ST_AsEWKT(ST_Envelope(ST_Collect(ST_SetSRID(ST_GeomFromGeoJSON(g), 4326))))"
            " FROM unnest(%s::text[]) AS g",
            (geoms,),
        ).fetchone()[0]
        reg = queries.register_dataset(conn, {
            "dataset_id": dataset_id,
            "version": version,
            "title": title,
            "description": "Deterministic SYNTHETIC test polygons; not real administrative boundaries.",
            "kind": "vector",
            "product_level": "other",
            "crs": "EPSG:4326",
            "bbox": bbox_ewkt,
            "checksum_sha256": checksum,
            "source": source,
            "provenance": {
                "synthetic": True,
                "loader": "cgis_db.fixtures",
                "file_name": path.name,
                "feature_count": len(feats),
                "note": "SYNTHETIC deterministic fixture - not real population or boundary data",
            },
            "status": "staged",
        })
        for f, g in zip(feats, geoms):
            props = f["properties"]
            conn.execute(
                "SELECT geo.upsert_admin_unit(%s, %s, %s,"
                " ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s, %s, %s, %s, %s)",
                (
                    props["unit_id"], props["name"], props.get("level"), g,
                    Jsonb({k: v for k, v in props.items() if k not in _UNIT_KEYS}),
                    dataset_id, version, props.get("source", source),
                    Jsonb({"synthetic": True, "file_name": path.name}),
                ),
            )
        if reg["status"] != "ready":
            queries.set_dataset_status(conn, dataset_id, version, "ready")
    return {"dataset_id": dataset_id, "version": version, "outcome": reg["outcome"],
            "units": len(feats), "checksum_sha256": checksum}
