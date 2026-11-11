# Catalog-driven pygeoapi publication: every ready catalog dataset becomes one collection
# (vector -> OGC API Features, raster -> OGC API Tiles). Writes the config + OpenAPI document and,
# with --refresh, reloads gunicorn when the config changed. Run as `python -m cgis_ogc.publish`.

from __future__ import annotations

import argparse
import copy
import hashlib
import logging
import os
import signal
import sys
from pathlib import Path

import yaml

from .catalog import DATASET_ID_RE, CatalogDataset, read_catalog
from .conformance import FEATURE_PROVIDER, TILE_PROVIDER
from .tms import CRS84, TILE_MATRIX_SETS, native_max_zoom

LOGGER = logging.getLogger("cgis_ogc.publish")

FEATURE_CRS = [
    CRS84,
    "http://www.opengis.net/def/crs/EPSG/0/4326",
    "http://www.opengis.net/def/crs/EPSG/0/3857",
]
# Optional catalog provenance key with publication hints ("filter" for vectors, "render" for rasters).
PUBLICATION_HINT_KEY = "ogc_publication"
FILTERABLE_VALUE_TYPES = (str, int, float, bool)


# Publication settings from the OGC_* / TILES_PUBLIC_URL env vars. Used by main and the unit tests.
class PublishSettings:
    # env defaults to os.environ; raises ValueError on unknown tile schemes.
    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self.vector_schema = env.get("OGC_VECTOR_SCHEMA", "geo")
        self.vector_table = env.get("OGC_VECTOR_TABLE", "admin_units")
        self.vector_id_field = env.get("OGC_VECTOR_ID_FIELD", "unit_id")
        self.vector_geom_field = env.get("OGC_VECTOR_GEOM_FIELD", "geom")
        # Published feature properties; other (internal) columns stay hidden. Empty value = every column.
        self.vector_properties = [p.strip() for p in env.get("OGC_VECTOR_PROPERTIES", "unit_id,name,level").split(",")
                                  if p.strip()]
        self.tile_schemes = [s for s in env.get("OGC_TILE_SCHEMES", "WebMercatorQuad,WorldCRS84Quad").split(",") if s]
        self.tile_colormap = env.get("OGC_TILE_COLORMAP", "viridis")
        self.tiles_public_url = env.get("TILES_PUBLIC_URL", "http://localhost:8080/tiles").rstrip("/")
        unknown = set(self.tile_schemes) - set(TILE_MATRIX_SETS)
        if unknown:
            raise ValueError(f"unsupported OGC_TILE_SCHEMES: {sorted(unknown)}")


# Collection extents (CRS84 bbox, plus the catalog year as a temporal extent). Used by *_resource.
def _extents(ds: CatalogDataset) -> dict:
    extents = {"spatial": {"bbox": list(ds.bbox), "crs": CRS84}}
    if ds.year:
        extents["temporal"] = {
            "begin": f"{ds.year:04d}-01-01T00:00:00Z",
            "end": f"{ds.year:04d}-12-31T23:59:59Z",
            "trs": "http://www.opengis.net/def/uom/ISO-8601/0/Gregorian",
        }
    return extents


# Optional logical group (provenance.group_id, e.g. india-level2-2011) if it is a safe id.
# Used by _collection and the unit tests.
def group_id(ds: CatalogDataset) -> str | None:
    value = (ds.provenance or {}).get("group_id")
    return value if isinstance(value, str) and DATASET_ID_RE.match(value) else None


# Common collection fields (title, description with catalog facts, keywords, extents). Used by *_resource.
# The fact order is parsed by the viewer (webapp catalog.js): "source" stays last (free text).
def _collection(ds: CatalogDataset, providers: list[dict], links: list[dict]) -> dict:
    facts = [f"catalog version {ds.version}"]
    if ds.crs:
        facts.append(f"native CRS {ds.crs}")
    if ds.units:
        facts.append(f"units {ds.units}")
    if ds.nodata is not None:
        facts.append(f"NoData {ds.nodata:g}")
    if group_id(ds):
        facts.append(f"group {group_id(ds)}")
    if ds.render:
        lo, hi = ds.render["rescale"]
        facts.append(f"display scale {ds.render['profile_id']} {lo:g}..{hi:g}")
    if ds.source:
        facts.append(f"source {ds.source}")
    keywords = [ds.kind, ds.product_level, ds.source, ds.units, str(ds.year) if ds.year else None]
    return {
        "type": "collection",
        "title": ds.title,
        "description": f"{(ds.description or ds.title).strip()} ({'; '.join(facts)})",
        "keywords": [w for w in keywords if w],
        "links": links,
        "extents": _extents(ds),
        "providers": providers,
    }


# Row filter scoping the shared vector table to one dataset: dataset_id (and dataset_version, so only the
# current catalog version is served) if the table has those columns, else the provenance hint {"ogc_publication": {"filter": {column: value}}}, else none.
# Used by vector_resource and the unit tests.
def vector_static_filter(ds: CatalogDataset, table_columns: list[str]) -> dict:
    if "dataset_id" in table_columns:
        if "dataset_version" in table_columns:
            return {"dataset_id": ds.dataset_id, "dataset_version": ds.version}
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


# pygeoapi resource for a vector dataset (CatalogPostgreSQLProvider). Used by build_config.
def vector_resource(ds: CatalogDataset, settings: PublishSettings, table_columns: list[str]) -> dict:
    provider = {
        "type": "feature",
        "name": FEATURE_PROVIDER,
        # Credentials stay ${VAR} references that pygeoapi substitutes at load time: no secrets on disk.
        "data": {
            "host": "${POSTGRES_HOST}",
            "port": "${POSTGRES_PORT:-5432}",
            "dbname": "${POSTGRES_DB}",
            "user": "${POSTGRES_USER}",
            "password": "${POSTGRES_PASSWORD}",
            "search_path": [settings.vector_schema, "public"],
        },
        "options": {"connect_timeout": 10},
        "id_field": settings.vector_id_field,
        "table": settings.vector_table,
        "geom_field": settings.vector_geom_field,
        "storage_crs": CRS84,  # PostGIS geometry(…,4326) is stored lon/lat
        "crs": list(FEATURE_CRS),
    }
    published = [c for c in settings.vector_properties if c in table_columns]
    if published:
        if settings.vector_id_field not in published:
            published.insert(0, settings.vector_id_field)
        provider["properties"] = published
    static_filter = vector_static_filter(ds, table_columns)
    if static_filter:
        provider["static_filter"] = static_filter
    return _collection(ds, [provider], [])


# Optional {"ogc_publication": {"render": {"rescale": [lo, hi]}}} hint, validated. Used by raster_resource.
def raster_rescale_hint(ds: CatalogDataset) -> list[float] | None:
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


# pygeoapi resource for a raster dataset (TiTilerTileProvider, PNG map tiles). Used by build_config.
def raster_resource(ds: CatalogDataset, settings: PublishSettings) -> dict:
    geographic = (ds.crs or "").upper() in {"EPSG:4326", "OGC:CRS84"}
    resolutions = [abs(r) for r in (ds.resolution_x, ds.resolution_y) if r]
    options = {
        "schemes": list(settings.tile_schemes),
        "zoom": {"min": 0, "max": native_max_zoom(min(resolutions) if resolutions else None, geographic)},
        "bbox": list(ds.bbox),
        "colormap_name": settings.tile_colormap,
    }
    # Portrayal precedence: shared render profile > the dataset's own rescale hint > TiTiler autoscale.
    if ds.render:
        options["rescale"] = list(ds.render["rescale"])
        options["colormap_name"] = ds.render["colormap"]
        options["render_profile"] = {k: ds.render[k] for k in ("profile_id", "title", "units", "description")}
    else:
        rescale = raster_rescale_hint(ds)
        if rescale:
            options["rescale"] = rescale
    provider = {
        "type": "tile",
        "name": TILE_PROVIDER,
        "data": ds.dataset_id,  # a catalog dataset_id, never a path or URL; TiTiler resolves it
        "format": {"name": "png", "mimetype": "image/png"},
        "options": options,
    }
    vendor = f"{settings.tiles_public_url}/datasets/{ds.dataset_id}"
    links = [{
        "type": "application/json",
        "rel": "alternate",
        "title": "TiTiler TileJSON (vendor-specific, not an OGC API resource)",
        "href": f"{vendor}/WebMercatorQuad/tilejson.json",
    }, {
        "type": "application/json",
        "rel": "describedby",
        "title": ("Dataset metadata: units, NoData, resolution, logical group, shared display scale "
                  "(vendor-specific TiTiler route, not an OGC API resource)"),
        "href": vendor,
    }]
    return _collection(ds, [provider], links)


# Base config with `resources` rebuilt from the catalog (unpublishable rows skipped with a warning).
# Used by render_config and the unit tests.
def build_config(base: dict, datasets: list[CatalogDataset], table_columns: list[str],
                 settings: PublishSettings) -> dict:
    resources: dict = {}
    for ds in sorted(datasets, key=lambda d: d.dataset_id):
        if ds.status != "ready":
            continue
        if not DATASET_ID_RE.match(ds.dataset_id or ""):
            LOGGER.warning("skipping dataset with unsafe id %r", ds.dataset_id)
        elif not ds.bbox:
            LOGGER.warning("skipping %s: no bbox in catalog", ds.dataset_id)
        elif ds.kind == "vector" and not table_columns:
            LOGGER.warning("skipping %s: vector table %s.%s not found",
                           ds.dataset_id, settings.vector_schema, settings.vector_table)
        elif ds.kind == "vector":
            resources[ds.dataset_id] = vector_resource(ds, settings, table_columns)
        elif ds.kind == "raster" and not ds.asset_path:
            LOGGER.warning("skipping %s: raster without asset_path", ds.dataset_id)
        elif ds.kind == "raster":
            resources[ds.dataset_id] = raster_resource(ds, settings)
        else:
            LOGGER.warning("skipping %s: unknown kind %r", ds.dataset_id, ds.kind)
    unscoped = [k for k, v in resources.items()
                if v["providers"][0]["type"] == "feature" and "static_filter" not in v["providers"][0]]
    if len(unscoped) > 1:
        LOGGER.warning("vector datasets %s all publish the whole %s table (no dataset_id column or filter hint)",
                       unscoped, settings.vector_table)
    config = copy.deepcopy(base)
    config["resources"] = resources
    return config


# Serialises a config as YAML with a "generated" header. Used by render_config and the unit tests.
def dump_config(config: dict) -> str:
    header = "# GENERATED by cgis_ogc.publish from catalog.current_datasets - do not edit by hand.\n"
    return header + yaml.safe_dump(config, sort_keys=False, default_flow_style=False, allow_unicode=True)


# Reads the catalog and returns the generated config text. Used by main.
def render_config(base_path: Path, settings: PublishSettings, wait_seconds: float) -> str:
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    datasets, columns = read_catalog(settings.vector_schema, settings.vector_table, wait_seconds)
    return dump_config(build_config(base, datasets, columns, settings))


# Writes a file via a temp file + rename so readers never see a partial file. Used by publish/write_openapi.
def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# Generates pygeoapi's OpenAPI document with the conformance adapter applied. Used by publish.
def write_openapi(config_path: Path, openapi_path: Path) -> None:
    from pygeoapi.openapi import generate_openapi_document
    from pygeoapi.util import yaml_load

    from . import conformance

    with config_path.open(encoding="utf-8") as fh:
        conformance.apply(yaml_load(fh))
    _write_atomic(openapi_path, generate_openapi_document(config_path, "yaml", fail_on_invalid_collection=True))


# Writes config + OpenAPI if the config changed (or OpenAPI is missing); returns True if changed. Used by main.
def publish(content: str, config_path: Path, openapi_path: Path) -> bool:
    old = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    changed = old != content
    if changed or not openapi_path.exists():
        _write_atomic(config_path, content)
        write_openapi(config_path, openapi_path)
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    LOGGER.info("published %d collection(s) (config %s, %s)",
                content.count("type: collection"), digest, "changed" if changed else "unchanged")
    return changed


# Sends SIGHUP to the gunicorn master so workers reload the new config. Used by main (--refresh).
def reload_server(pidfile: Path) -> None:
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        LOGGER.warning("no gunicorn pidfile at %s; restart the service to apply changes", pidfile)
        return
    os.kill(pid, signal.SIGHUP)
    LOGGER.info("sent SIGHUP to gunicorn master %d", pid)


# CLI entry point. Used by docker/entrypoint.sh (serve, refresh loop, `publish`/`refresh` commands).
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Catalog-driven pygeoapi publication.")
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

    content = render_config(args.base, PublishSettings(), args.wait)
    if args.dry_run:
        sys.stdout.write(content)
        return 0
    args.config.parent.mkdir(parents=True, exist_ok=True)
    if publish(content, args.config, args.openapi) and args.refresh:
        reload_server(args.pidfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
