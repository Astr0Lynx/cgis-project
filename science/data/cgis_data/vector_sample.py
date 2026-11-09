"""Vector sample for the importer: admin polygons intersecting a bbox (real geoBoundaries, DIAGNOSTIC – not Census codes)."""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import box, mapping, shape

from .fetch import sha256_file


def make_vector_sample(adm_geojson: str, bbox, out_dir: Path, name: str, level: str, source_sha256: str,
                       upstream_url: str, license_: str, id_prefix: str) -> dict:
    """Write `<out_dir>/vectors/<name>.geojson` (whole polygons that intersect bbox; geometry unchanged) and return a manifest entry."""
    fc = json.load(open(adm_geojson))
    bb = box(*bbox)
    feats = []
    for f in fc["features"]:
        g = shape(f["geometry"])
        if not g.is_valid or g.is_empty:
            raise ValueError(f"source polygon {f['properties'].get('shapeID')} invalid (importer does not repair)")
        if g.intersects(bb):
            p = f["properties"]
            feats.append({"type": "Feature", "geometry": mapping(g),
                          "properties": {"unit_id": p["shapeID"], "name": p["shapeName"], "level": level}})
    if not feats:
        raise ValueError("no polygons intersect bbox")
    out = Path(out_dir) / "vectors" / f"{name}.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return {
        "dataset_id": name, "version": 1,
        "title": f"geoBoundaries IND {level.upper()} units intersecting sample bbox (DIAGNOSTIC; no Census codes)",
        "description": "Generic admin polygons, not Census 2011 geocodes; unmodified geometries selected by bbox intersection.",
        "kind": "vector", "product_level": "source", "year": None, "source": "geoBoundaries gbOpen commit 9469f09",
        "file": f"vectors/{out.name}", "id_property": "unit_id", "name_property": "name", "level_property": "level",
        "unit_id_prefix": id_prefix, "checksum_sha256": sha256_file(out),
        "provenance": {"upstream_url": upstream_url, "upstream_license": license_,
                       "upstream_sha256": source_sha256, "selection": f"features intersecting bbox {list(bbox)}",
                       "n_features": len(feats), "authoritative_for_census_join": False,
                       "processing_steps": ["select by bbox intersection; geometry and CRS (CRS84) unchanged",
                                            "rename shapeID/shapeName -> unit_id/name; add level"]},
    }
