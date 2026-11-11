# Input readers: checksummed sources, the v1 Level 1 manifest, Census selection/PCA/polygons, the LULC legend
# and raster or vector land cover resampled to the fine grid.

import csv
import json
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import fiona
import numpy as np
import rasterio
import shapely
from rasterio.enums import MergeAlg, Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import transform as window_transform
from shapely.geometry import box, shape
from shapely.strtree import STRtree

from ingestion.manifest import SHA256_RE, load_manifest, parse_entry
from ingestion.importer import sha256_file

from .grid import AREA_EPS, CRS, north_up, polygon_in_grid

LULC_NODATA = -2147483648
MAX_VECTOR_FEATURES = 100_000
MAX_VECTOR_VERTICES = 2_000_000
MORPHOLOGIES = {"urban", "peri-urban", "rural"}
MODELS = ("soft", "hard")


# Reads a UTF-8 (BOM-tolerant) JSON file; used across the package and tests.
def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


# Fails unless `expected` is a valid SHA-256 equal to the file's; used by Source.read and read_level1.
def _verify(path, expected):
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        raise ValueError(f"valid sha256 is required for {path.name}")
    if sha256_file(path) != expected:
        raise ValueError(f"checksum mismatch for {path.name}")


# One configured input file with its metadata (citation, licence, access record, checksum).
@dataclass(frozen=True)
class Source:
    path: Path
    metadata: dict

    # Validates metadata (real mode needs an authorised access record) and the checksum; used by pipeline.run.
    @classmethod
    def read(cls, root, raw, synthetic):
        fields = ("dataset_id", "release", "citation", "license")
        if any(not isinstance(raw.get(k), str) or not raw[k].strip() for k in fields):
            raise ValueError(f"source requires {', '.join(fields)}")
        if not synthetic and (raw.get("access_status") != "authorized"
                              or not isinstance(raw.get("access_reference"), str)
                              or not raw["access_reference"].strip()):
            raise ValueError(f"authorized analytical access record required for {raw['dataset_id']}")
        path = (root / raw["path"]).resolve()
        _verify(path, raw.get("sha256"))
        return cls(path, {k: v for k, v in raw.items() if k != "path"})


# Loads the configured 2011 Level 1 entry from its v1 manifest and verifies it (real mode: accepted national
# checksum and provenance); used by pipeline.run.
def read_level1(root, raw, synthetic):
    path = root / raw["manifest"]
    if read_json(path).get("manifest_version") != 1:
        raise ValueError("Level 1 requires a v1 manifest")
    base, entries = load_manifest(path)
    matches = [entry for entry in entries if (entry.get("dataset_id"), entry.get("version"))
               == (raw["dataset_id"], raw["version"])]
    if len(matches) != 1:
        raise ValueError("configured Level 1 ID/version not found")
    spec = parse_entry(matches[0], base)
    if (spec.kind, spec.product_level, spec.year, spec.units) != ("raster", "level1", 2011, "people_per_cell"):
        raise ValueError("Level 1 must be a 2011 people-per-cell count raster")
    if not synthetic and (spec.is_synthetic or spec.dataset_id != "india-l1-pop-2011"
                          or raw.get("accepted_sha256") != spec.checksum_sha256
                          or spec.provenance.get("synthetic") is not False
                          or not spec.provenance.get("model_commit")):
        raise ValueError("real mode requires the accepted national Level 1 checksum and provenance")
    _verify(spec.file, spec.checksum_sha256)
    return spec


# A selected settlement with its Census total and EPSG:6933 polygon.
@dataclass(frozen=True)
class Town:
    census_id: str
    slug: str
    name: str
    region: str
    morphology: str
    population: int
    polygon: object


# Reads a CSV keyed by unique, well-formed census_id; used by read_towns.
def _by_id(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        out = {}
        for row in csv.DictReader(stream):
            census_id = row.get("census_id", "")
            if not re.fullmatch(r"[A-Za-z0-9-]{1,48}", census_id) or census_id in out:
                raise ValueError(f"missing, invalid, or duplicate census_id in {path.name}: {census_id!r}")
            out[census_id] = row
        return out


# Joins selection, PCA totals and polygons by Census ID and enforces names, vintage, coverage of strata and
# no polygon overlaps; used by pipeline.run.
def read_towns(selection, pca, boundaries, synthetic):
    selected, census = _by_id(selection.path), _by_id(pca.path)
    if not 1 <= len(selected) <= 100:
        raise ValueError("selection requires 1 to 100 settlements")
    if boundaries.path.stat().st_size > 64 * 1024 ** 2:
        raise ValueError("selected settlement GeoJSON exceeds 64 MiB")
    document = read_json(boundaries.path)
    if document.get("type") != "FeatureCollection" or "crs" in document:
        raise ValueError("study areas must be an RFC 7946 WGS84 FeatureCollection")
    geometries = {}
    for feature in document["features"]:
        census_id = feature["properties"].get("census_id")
        if not isinstance(census_id, str) or census_id not in selected or census_id in geometries:
            raise ValueError(f"unexpected, missing, or duplicate polygon Census ID: {census_id!r}")
        if not synthetic and (feature["properties"].get("name") != selected[census_id].get("name")
                              or feature["properties"].get("census_year") != 2011):
            raise ValueError(f"polygon Census name or vintage disagrees for {census_id}")
        geo = shape(feature["geometry"])
        xmin, ymin, xmax, ymax = geo.bounds
        if not (-180 <= xmin <= xmax <= 180 and -90 <= ymin <= ymax <= 90):
            raise ValueError("invalid WGS84 polygon coordinates")
        if not synthetic and not (65 <= xmin <= xmax <= 100 and 5 <= ymin <= ymax <= 39):
            raise ValueError("real Census polygon falls outside the India study region")
        geometries[census_id] = polygon_in_grid(geo, "EPSG:4326")
    towns, slugs = [], set()
    for census_id, row in sorted(selected.items()):
        if census_id not in census or census_id not in geometries:
            raise ValueError(f"missing PCA total or polygon for {census_id}")
        pca_row = census[census_id]
        slug, name, region, morphology = (row[k].strip() for k in ("slug", "name", "region", "morphology"))
        population = pca_row.get("population_2011", "")
        if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 80 or slug in slugs
                or not name or not region or morphology not in MORPHOLOGIES):
            raise ValueError(f"invalid settlement selection for {census_id}")
        if (not re.fullmatch(r"[0-9]{1,10}", population)
                or row.get("population_2011") != population
                or row.get("name") != pca_row.get("name")):
            raise ValueError(f"selection disagrees with PCA name or total for {census_id}")
        if not synthetic and (not re.fullmatch(r"[0-9]{2}-[0-9]{3}-[0-9]{5}-[0-9]{6}", census_id)
                              or row.get("status") != "selected" or not row.get("rationale", "").strip()):
            raise ValueError(f"real selection needs full Census code, selected status and rationale: {census_id}")
        slugs.add(slug)
        towns.append(Town(census_id, slug, name, region, morphology, int(population), geometries[census_id]))
    if not synthetic and (len(towns) < 6 or len({t.region for t in towns}) < 4
                          or {t.morphology for t in towns} != MORPHOLOGIES):
        raise ValueError("real run requires six settlements, four regions and all morphology strata")
    for a, b in combinations(towns, 2):
        if a.polygon.intersects(b.polygon) and a.polygon.intersection(b.polygon).area > AREA_EPS:
            raise ValueError(f"overlapping Census polygons: {a.census_id} and {b.census_id}")
    return towns


# One legend class with its soft sensitivity weight and hard-mask eligibility.
@dataclass(frozen=True)
class LandClass:
    code: int
    label: str
    source_value: str
    soft: float
    hard: float


# Builds {code: LandClass} from legend.json and method.class_weights, requiring a one-to-one match; used by
# pipeline.run.
def read_legend(source, model):
    rows = read_json(source.path)["classes"]
    weights = model["class_weights"]
    result = {}
    for row in rows:
        code, label, token = row["code"], row["label"], row["source_value"]
        if (type(code) is not int or not LULC_NODATA < code <= 2147483647 or code in result
                or not isinstance(label, str) or not label.strip()
                or not isinstance(token, str) or not token.strip() or label not in weights):
            raise ValueError("invalid legend code/label or missing approved class mapping")
        rule = weights[label]
        soft, hard = rule["soft"], rule["hard"]
        if type(soft) not in (float, int) or not 0 <= soft <= 1 or type(hard) is not bool or (hard and not soft):
            raise ValueError(f"invalid class weight or mask for {label}")
        result[code] = LandClass(code, label, token, float(soft), float(hard))
    if (not result or {c.label for c in result.values()} != set(weights)
            or len(result) != len(weights) or len(result) != len({c.source_value for c in result.values()})):
        raise ValueError("legend and model class weights must match one-to-one")
    return result


# Categorical LULC raster warped (nearest) onto the fine grid; used by model.build_town.
class RasterLandCover:
    # Opens the raster and a WarpedVRT on the fine grid inside `stack`.
    def __init__(self, stack, source, grid, legend):
        src = stack.enter_context(rasterio.open(source.path))
        north_up(src)
        if src.count != 1 or src.dtypes[0] not in ("uint8", "int8", "uint16", "int16", "int32") or src.nodata is None:
            raise ValueError("categorical LULC raster needs one integer band with explicit NoData")
        if src.nodata in legend:
            raise ValueError("LULC NoData conflicts with a legend class")
        self.vrt = stack.enter_context(WarpedVRT(
            src, crs=CRS, transform=grid.transform, width=grid.width, height=grid.height,
            dtype="int32", nodata=LULC_NODATA, resampling=Resampling.nearest, warp_mem_limit=64,
        ))

    # Class codes for one fine-grid window.
    def read(self, window):
        return self.vrt.read(1, window=window)


# Vector LULC (GeoPackage/GeoJSON) rasterised at fine-cell centres; used by model.build_town.
class VectorLandCover:
    # Loads features inside the grid bounds, maps class tokens to codes and builds a spatial index.
    def __init__(self, source, grid, legend):
        self.grid = grid
        field = source.metadata["class_field"]
        code_by_token = {rule.source_value: code for code, rule in legend.items()}
        options = {"layer": source.metadata["layer"]} if source.metadata.get("layer") else {}
        self.geometries, self.codes = [], []
        vertices = 0
        if source.path.suffix.lower() not in (".gpkg", ".geojson"):
            raise ValueError("prepare Bhuvan shapefile as a checksummed local GeoPackage or GeoJSON")
        with fiona.open(source.path, **options) as features:
            if not features.crs or field not in features.schema["properties"]:
                raise ValueError("vector LULC needs a CRS and the configured source class field")
            bounds = transform_bounds(CRS, features.crs.to_wkt(), *grid.bounds, densify_pts=21)
            for feature in features.filter(bbox=bounds):
                token = str(feature.properties[field])
                if token not in code_by_token:
                    raise ValueError(f"unknown LULC class in approved legend: {token!r}")
                geometry = polygon_in_grid(shape(feature.geometry), features.crs.to_wkt())
                vertices += shapely.get_num_coordinates(geometry)
                if len(self.geometries) >= MAX_VECTOR_FEATURES or vertices > MAX_VECTOR_VERTICES:
                    raise ValueError("vector LULC exceeds settlement feature or vertex budget")
                self.geometries.append(geometry)
                self.codes.append(code_by_token[token])
        self.tree = STRtree(self.geometries)

    # Class codes for one window; fails on conflicting positive-area overlaps at a cell centre.
    def read(self, window):
        affine = window_transform(window, self.grid.transform)
        height, width = int(window.height), int(window.width)
        tile = box(*rasterio.transform.array_bounds(height, width, affine))
        indexes = sorted(self.tree.query(tile, predicate="intersects"), key=lambda i: (self.codes[i], i))
        if not indexes:
            return np.full((height, width), LULC_NODATA, dtype="int32")
        shapes = [(self.geometries[i], self.codes[i]) for i in indexes]
        overlap = rasterize(((g, 1) for g, _ in shapes), (height, width), transform=affine,
                            dtype="int32", merge_alg=MergeAlg.add)
        if np.any(overlap > 1):
            # Rasterization can double-hit a centre on an exact shared edge. Positive-area overlaps
            # with conflicting labels are invalid; same-class overlaps and shared edges are harmless.
            for row, col in zip(*np.nonzero(overlap > 1)):
                point = shapely.Point(*(affine * (col + 0.5, row + 0.5)))
                hits = self.tree.query(point, predicate="intersects")
                for a, b in combinations(hits, 2):
                    if (self.codes[a] != self.codes[b]
                            and self.geometries[a].intersection(self.geometries[b]).area > AREA_EPS):
                        raise ValueError("conflicting vector LULC classes overlap at a target cell centre")
        return rasterize(shapes, (height, width), transform=affine, fill=LULC_NODATA, dtype="int32")
