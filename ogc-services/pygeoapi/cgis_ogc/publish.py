"""Catalog-driven pygeoapi publication.

Every ``ready`` dataset in ``catalog.current_datasets`` becomes one OGC API
collection whose ``collectionId`` is the catalog ``dataset_id``:

* ``kind = vector`` -> OGC API - Features (PostgreSQL provider over the contract
  vector table, ``geo.admin_units`` by default);
* ``kind = raster`` with an ``asset_path`` -> OGC API - Tiles map tilesets (PNG),
  rendered by the internal restricted TiTiler service.

No dataset IDs are hard-coded: the resource list is rebuilt from the catalog on
every publish, so registering a new dataset needs no configuration edits.

Usage (inside the ``ogc`` container)::

    python -m cgis_ogc.publish            # write config + OpenAPI once
    python -m cgis_ogc.publish --refresh  # same, then reload gunicorn if changed
    python -m cgis_ogc.publish --dry-run  # print generated config
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import logging
import os
import re
import signal
import sys
from pathlib import Path

import yaml

from .catalog import CatalogDataset, CatalogReader, db_params_from_env
from .tms import TILE_MATRIX_SETS, native_max_zoom

LOGGER = logging.getLogger("cgis_ogc.publish")

COLLECTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
FEATURE_CRS = [
    CRS84,
    "http://www.opengis.net/def/crs/EPSG/0/4326",
    "http://www.opengis.net/def/crs/EPSG/0/3857",
]
FEATURE_PROVIDER = "cgis_ogc.pygeoapi_plugins.postgresql.CatalogPostgreSQLProvider"
TILE_PROVIDER = "cgis_ogc.pygeoapi_plugins.titiler_tiles.TiTilerTileProvider"
# Optional, documented catalog provenance hint for narrowing a vector dataset to
# a subset of rows when the vector table has no dataset_id column.
PUBLICATION_HINT_KEY = "ogc_publication"
FILTERABLE_VALUE_TYPES = (str, int, float, bool)


class PublishSettings:
    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self.vector_schema = env.get("OGC_VECTOR_SCHEMA", "geo")
        self.vector_table = env.get("OGC_VECTOR_TABLE", "admin_units")
        self.vector_id_field = env.get("OGC_VECTOR_ID_FIELD", "unit_id")
        self.vector_geom_field = env.get("OGC_VECTOR_GEOM_FIELD", "geom")
        self.tile_schemes = [s for s in env.get("OGC_TILE_SCHEMES", "WebMercatorQuad,WorldCRS84Quad").split(",") if s]
        self.tile_colormap = env.get("OGC_TILE_COLORMAP", "viridis")
        self.tiles_public_url = env.get("TILES_PUBLIC_URL", "http://localhost:8080/tiles").rstrip("/")
        unknown = set(self.tile_schemes) - set(TILE_MATRIX_SETS)
        if unknown:
            raise ValueError(f"unsupported OGC_TILE_SCHEMES: {sorted(unknown)}")


def _db_data(settings: PublishSettings) -> dict:
    # Credentials stay as ${VAR} references; pygeoapi substitutes them from the
    # environment at load time, so the generated file holds no secrets.
    return {
        "host": "${POSTGRES_HOST}",
        "port": "${POSTGRES_PORT:-5432}",
        "dbname": "${POSTGRES_DB}",
        "user": "${POSTGRES_USER}",
        "password": "${POSTGRES_PASSWORD}",
        "search_path": [settings.vector_schema, "public"],
    }


def _extents(ds: CatalogDataset) -> dict:
    extents = {"spatial": {"bbox": list(ds.bbox), "crs": CRS84}}
    if ds.year:
        extents["temporal"] = {
            "begin": f"{ds.year:04d}-01-01T00:00:00Z",
            "end": f"{ds.year:04d}-12-31T23:59:59Z",
            "trs": "http://www.opengis.net/def/uom/ISO-8601/0/Gregorian",
        }
    return extents


def _keywords(ds: CatalogDataset) -> list[str]:
    words = [ds.kind, ds.product_level, ds.source, ds.units]
    if ds.year:
        words.append(str(ds.year))
    return [w for w in words if w]


def _description(ds: CatalogDataset) -> str:
    facts = [f"catalog version {ds.version}"]
    if ds.crs:
        facts.append(f"native CRS {ds.crs}")
    if ds.units:
        facts.append(f"units {ds.units}")
    if ds.nodata is not None:
        facts.append(f"NoData {ds.nodata:g}")
    if ds.source:
        facts.append(f"source {ds.source}")
    base = (ds.description or ds.title).strip()
    return f"{base} ({'; '.join(facts)})"


def vector_static_filter(ds: CatalogDataset, table_columns: list[str]) -> dict:
    """Row filter that scopes the shared vector table to this dataset.

    Preference order: a ``dataset_id`` column on the table (proposed in
    docs/contract-change-requests/agent4.md), else the optional provenance hint
    ``{"ogc_publication": {"filter": {<column>: <value>}}}``, else no filter.
    """
    if "dataset_id" in table_columns:
        return {"dataset_id": ds.dataset_id}
    hint = (ds.provenance or {}).get(PUBLICATION_HINT_KEY) or {}
    wanted = hint.get("filter") or {}
    if not isinstance(wanted, dict):
        LOGGER.warning("%s: ignoring non-object %s.filter", ds.dataset_id, PUBLICATION_HINT_KEY)
        return {}
    accepted = {}
    for column, value in wanted.items():
        if column not in table_columns or not isinstance(value, FILTERABLE_VALUE_TYPES):
            LOGGER.warning("%s: ignoring filter on %r", ds.dataset_id, column)
            continue
        accepted[column] = value
    return accepted


def vector_resource(ds: CatalogDataset, settings: PublishSettings, table_columns: list[str]) -> dict:
    provider = {
        "type": "feature",
        "name": FEATURE_PROVIDER,
        "data": _db_data(settings),
        "options": {"connect_timeout": 10},
        "id_field": settings.vector_id_field,
        "table": settings.vector_table,
        "geom_field": settings.vector_geom_field,
        "storage_crs": CRS84,  # PostGIS geometry(…,4326) is stored lon/lat
        "crs": list(FEATURE_CRS),
    }
    static_filter = vector_static_filter(ds, table_columns)
    if static_filter:
        provider["static_filter"] = static_filter
    return {
        "type": "collection",
        "title": ds.title,
        "description": _description(ds),
        "keywords": _keywords(ds),
        "links": [],
        "extents": _extents(ds),
        "providers": [provider],
    }


def raster_resource(ds: CatalogDataset, settings: PublishSettings) -> dict:
    geographic = (ds.crs or "").upper() in {"EPSG:4326", "OGC:CRS84"}
    resolutions = [abs(r) for r in (ds.resolution_x, ds.resolution_y) if r]
    max_zoom = native_max_zoom(min(resolutions) if resolutions else None, geographic)
    tilejson = f"{settings.tiles_public_url}/datasets/{ds.dataset_id}/WebMercatorQuad/tilejson.json"
    options = {
        "schemes": list(settings.tile_schemes),
        "zoom": {"min": 0, "max": max_zoom},
        "bbox": list(ds.bbox),
        "colormap_name": settings.tile_colormap,
    }
    rescale = raster_rescale_hint(ds)
    if rescale:
        options["rescale"] = rescale
    return {
        "type": "collection",
        "title": ds.title,
        "description": _description(ds),
        "keywords": _keywords(ds),
        "links": [{
            "type": "application/json",
            "rel": "alternate",
            "title": "TiTiler TileJSON (vendor-specific, not an OGC API resource)",
            "href": tilejson,
        }],
        "extents": _extents(ds),
        "providers": [{
            "type": "tile",
            "name": TILE_PROVIDER,
            # A catalog dataset_id, never a path or URL; TiTiler resolves it.
            "data": ds.dataset_id,
            "format": {"name": "png", "mimetype": "image/png"},
            "options": options,
        }],
    }


def raster_rescale_hint(ds: CatalogDataset) -> list[float] | None:
    """Optional ``{"ogc_publication": {"render": {"rescale": [lo, hi]}}}`` catalog hint."""
    render = ((ds.provenance or {}).get(PUBLICATION_HINT_KEY) or {}).get("render") or {}
    value = render.get("rescale") if isinstance(render, dict) else None
    if value is None:
        return None
    if (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
            and value[1] > value[0]):
        return [float(value[0]), float(value[1])]
    LOGGER.warning("%s: ignoring invalid render.rescale %r", ds.dataset_id, value)
    return None


def build_resources(datasets: list[CatalogDataset], table_columns: list[str], settings: PublishSettings) -> dict:
    resources: dict = {}
    for ds in sorted(datasets, key=lambda d: d.dataset_id):
        if ds.status != "ready":
            continue
        if not COLLECTION_ID_RE.match(ds.dataset_id or ""):
            LOGGER.warning("skipping dataset with unsafe id %r", ds.dataset_id)
            continue
        if not ds.bbox:
            LOGGER.warning("skipping %s: no bbox in catalog", ds.dataset_id)
            continue
        if ds.kind == "vector":
            if not table_columns:
                LOGGER.warning("skipping %s: vector table %s.%s not found",
                               ds.dataset_id, settings.vector_schema, settings.vector_table)
                continue
            resources[ds.dataset_id] = vector_resource(ds, settings, table_columns)
        elif ds.kind == "raster":
            if not ds.asset_path:
                LOGGER.warning("skipping %s: raster without asset_path", ds.dataset_id)
                continue
            resources[ds.dataset_id] = raster_resource(ds, settings)
        else:
            LOGGER.warning("skipping %s: unknown kind %r", ds.dataset_id, ds.kind)
    unscoped = [k for k, v in resources.items()
                if v["providers"][0]["type"] == "feature" and "static_filter" not in v["providers"][0]]
    if len(unscoped) > 1:
        LOGGER.warning("vector datasets %s all publish the whole %s table; see contract change request",
                       unscoped, settings.vector_table)
    return resources


def build_config(base: dict, datasets: list[CatalogDataset], table_columns: list[str],
                 settings: PublishSettings) -> dict:
    config = copy.deepcopy(base)
    config["resources"] = build_resources(datasets, table_columns, settings)
    return config


def dump_config(config: dict) -> str:
    header = "# GENERATED by cgis_ogc.publish from catalog.current_datasets - do not edit by hand.\n"
    return header + yaml.safe_dump(config, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def generate_openapi(config_path: Path, openapi_path: Path) -> None:
    from .openapi import write_openapi

    write_openapi(config_path, openapi_path)


def publish(base_path: Path, config_path: Path, openapi_path: Path, reader: CatalogReader,
            settings: PublishSettings) -> bool:
    """Regenerate config + OpenAPI. Returns True if the config changed."""
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    datasets, columns = reader.read(settings.vector_schema, settings.vector_table)
    content = dump_config(build_config(base, datasets, columns, settings))
    old = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    changed = old != content
    if changed or not openapi_path.exists():
        _write_atomic(config_path, content)
        generate_openapi(config_path, openapi_path)
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    LOGGER.info("published %d collection(s) (config %s, %s)",
                content.count("type: collection"), digest, "changed" if changed else "unchanged")
    return changed


def reload_server(pidfile: Path) -> None:
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        LOGGER.warning("no gunicorn pidfile at %s; restart the service to apply changes", pidfile)
        return
    os.kill(pid, signal.SIGHUP)
    LOGGER.info("sent SIGHUP to gunicorn master %d", pid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", type=Path, default=Path(os.environ.get("CGIS_OGC_BASE_CONFIG", "/cgis/config/pygeoapi.base.yml")))
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("PYGEOAPI_CONFIG", "/cgis/runtime/pygeoapi-config.yml")))
    parser.add_argument("--openapi", type=Path, default=Path(os.environ.get("PYGEOAPI_OPENAPI", "/cgis/runtime/pygeoapi-openapi.yml")))
    parser.add_argument("--pidfile", type=Path, default=Path(os.environ.get("CGIS_OGC_PIDFILE", "/cgis/runtime/gunicorn.pid")))
    parser.add_argument("--wait", type=float, default=float(os.environ.get("CGIS_OGC_DB_WAIT", "0")),
                        help="seconds to wait for the database")
    parser.add_argument("--refresh", action="store_true", help="reload gunicorn if the config changed")
    parser.add_argument("--dry-run", action="store_true", help="print generated config and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("CGIS_OGC_LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")

    settings = PublishSettings()
    reader = CatalogReader(db_params_from_env(), wait_seconds=args.wait)
    if args.dry_run:
        base = yaml.safe_load(args.base.read_text(encoding="utf-8"))
        datasets, columns = reader.read(settings.vector_schema, settings.vector_table)
        sys.stdout.write(dump_config(build_config(base, datasets, columns, settings)))
        return 0
    args.config.parent.mkdir(parents=True, exist_ok=True)
    changed = publish(args.base, args.config, args.openapi, reader, settings)
    if args.refresh and changed:
        reload_server(args.pidfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
