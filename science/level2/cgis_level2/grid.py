# Equal-area geometry: the 23.5 m EPSG:6933 fine grid, cell ownership between settlement polygons and exact
# intersections with native Level 1 cells.

from dataclasses import dataclass
from math import ceil, floor

import numpy as np
import rasterio
import shapely
from affine import Affine
from pyproj import Transformer
from rasterio.windows import Window
from shapely.ops import transform

CRS = "EPSG:6933"
PIXEL = 23.5
CELL_AREA = PIXEL ** 2
BLOCK = 256
AREA_EPS = 1e-7
MAX_FINE_CELLS = 25_000_000
MAX_COARSE_CELLS = 2_000_000


# Validates a (Multi)Polygon and projects it to EPSG:6933 (densified if geographic); used by inputs.read_towns
# and VectorLandCover.
def polygon_in_grid(geometry, src_crs):
    if geometry is None or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("expected a Polygon or MultiPolygon")
    if not geometry.is_valid or geometry.is_empty or not np.all(np.isfinite(geometry.bounds)):
        raise ValueError("invalid, empty, or nonfinite polygon")
    if src_crs != CRS:
        project = Transformer.from_crs(src_crs, CRS, always_xy=True)
        if project.source_crs.is_geographic:
            geometry = shapely.segmentize(geometry, 0.001)
        geometry = transform(project.transform, geometry)
    if not geometry.is_valid or not np.isfinite(geometry.area) or geometry.area <= AREA_EPS:
        raise ValueError("invalid or degenerate polygon after projection")
    shapely.prepare(geometry)
    return geometry


# Town window on the shared 23.5 m lattice anchored at x = y = 0; used by model.build_town.
@dataclass(frozen=True)
class FineGrid:
    transform: Affine
    width: int
    height: int

    # Snaps the polygon bounds outward to the lattice and enforces the fine-cell capacity limit.
    @classmethod
    def for_polygon(cls, geometry):
        left, bottom, right, top = geometry.bounds
        x0, x1 = floor(left / PIXEL), ceil(right / PIXEL)
        y0, y1 = floor(bottom / PIXEL), ceil(top / PIXEL)
        if (x1 - x0) * (y1 - y0) > MAX_FINE_CELLS:
            raise ValueError("settlement extent exceeds the fine-grid capacity limit")
        return cls(Affine(PIXEL, 0, PIXEL * x0, 0, -PIXEL, PIXEL * y1), x1 - x0, y1 - y0)

    # (left, bottom, right, top) in EPSG:6933; used for LULC reads, CoarseGrid and the report.
    @property
    def bounds(self):
        return rasterio.transform.array_bounds(self.height, self.width, self.transform)

    # Yields BLOCK x BLOCK processing windows in row-major order; used by model.build_town.
    def windows(self):
        for row in range(0, self.height, BLOCK):
            for col in range(0, self.width, BLOCK):
                yield Window(col, row, min(BLOCK, self.width - col), min(BLOCK, self.height - row))


# Intersection area of each cell with the polygon (full area where covered) and the covered flags; used by owned_cells.
def _areas(geometry, cells):
    whole = shapely.covers(geometry, cells)
    area = np.full(len(cells), CELL_AREA, dtype="float64")
    area[~whole] = shapely.area(shapely.intersection(geometry, cells[~whole]))
    return area, whole


# Fine cells owned by one town in one window, with centres, areas and clipped edge geometry.
@dataclass
class OwnedCells:
    rows: np.ndarray
    cols: np.ndarray
    x: np.ndarray
    y: np.ndarray
    area: np.ndarray
    edge: np.ndarray  # clipped polygon for edge cells, None for whole cells


# Cells of `window` owned by this town: each cell goes to the largest positive-area claimant, ties to the
# lowest Census ID; used by model.block_inputs.
def owned_cells(grid, window, census_id, polygon, neighbors):
    rows, cols = np.indices((int(window.height), int(window.width)))
    xf, yf = grid.transform * (cols.ravel() + window.col_off, rows.ravel() + window.row_off)
    cells = shapely.box(xf, yf - PIXEL, xf + PIXEL, yf)
    touches = shapely.intersects(polygon, cells)
    rows, cols = rows.ravel()[touches], cols.ravel()[touches]
    xf, yf, cells = xf[touches], yf[touches], cells[touches]
    area, whole = _areas(polygon, cells)
    keep = area > AREA_EPS
    for other_id, other_polygon in neighbors:
        compare = np.flatnonzero(keep & ~whole & shapely.intersects(other_polygon, cells))
        other_area, _ = _areas(other_polygon, cells[compare])
        delta = np.rint(area[compare] / AREA_EPS) - np.rint(other_area / AREA_EPS)
        loses = (delta < 0) | ((delta == 0) & (other_id < census_id))
        keep[compare[loses]] = False
    edge = np.full(len(cells), None, dtype=object)
    boundary = keep & ~whole
    edge[boundary] = shapely.intersection(polygon, cells[boundary])
    return OwnedCells(rows[keep], cols[keep], xf[keep], yf[keep], area[keep], edge[keep])


# Rejects rasters that are not north-up with a recognised EPSG CRS; used by CoarseGrid and RasterLandCover.
def north_up(src):
    affine = src.transform
    if (not src.crs or not src.crs.to_epsg() or affine.b != 0 or affine.d != 0
            or affine.a <= 0 or affine.e >= 0 or not np.all(np.isfinite(tuple(affine)))):
        raise ValueError("raster requires a north-up grid and a recognized EPSG CRS")


# Bounded native Level 1 window around a town, as EPSG:6933 rectangles with a per-area density; used by
# model.build_town.
class CoarseGrid:
    # Reads and validates the window, projects cell edges and computes density and the town's Level 1 total.
    def __init__(self, src, band, fine, polygon):
        north_up(src)
        if src.crs.to_epsg() not in (4326, 6933) or band > src.count or src.nodatavals[band - 1] is None:
            raise ValueError("Level 1 requires EPSG:4326 or EPSG:6933, a valid band and declared NoData")
        to_native = Transformer.from_crs(CRS, src.crs.to_string(), always_xy=True)
        left, bottom, right, top = fine.bounds
        x0, y0 = to_native.transform(left, bottom)
        x1, y1 = to_native.transform(right, top)
        c0, r0 = ~src.transform * (x0, y1)
        c1, r1 = ~src.transform * (x1, y0)
        col0, col1 = max(0, floor(c0) - 1), min(src.width, ceil(c1) + 1)
        row0, row1 = max(0, floor(r0) - 1), min(src.height, ceil(r1) + 1)
        width, height = col1 - col0, row1 - row0
        if min(width, height) <= 0:
            raise ValueError("Level 1 does not overlap this settlement")
        if width * height > MAX_COARSE_CELLS:
            raise ValueError("settlement exceeds the native Level 1 window capacity")
        project = Transformer.from_crs(src.crs.to_string(), CRS, always_xy=True)
        native_x = src.transform.c + np.arange(col0, col1 + 1) * src.transform.a
        native_y = src.transform.f + np.arange(row0, row1 + 1) * src.transform.e
        self.x = np.asarray(project.transform(native_x, np.full_like(native_x, native_y[0]))[0])
        self.y = -np.asarray(project.transform(np.full_like(native_y, native_x[0]), native_y)[1])
        if min(np.min(np.diff(self.x)), np.min(np.diff(self.y))) < PIXEL:
            raise ValueError("native Level 1 cells must be at least as wide and tall as the Level 2 grid")
        self.col0, self.row0 = col0, row0
        counts = src.read(band, window=Window(col0, row0, width, height), masked=True).astype("float64")
        valid_counts = counts.compressed()
        if np.any(~np.isfinite(valid_counts)) or np.any(valid_counts < 0):
            raise ValueError("invalid negative or nonfinite Level 1 counts")
        self.valid = ~np.ma.getmaskarray(counts)
        self.density = counts.filled(0) / (np.diff(self.y)[:, None] * np.diff(self.x)[None, :])
        self.shape = self.density.shape
        self.town_people, self.town_valid_area = self._polygon_total(polygon)

    # Level 1 people and valid area on the whole Census polygon (observed support), computed blockwise.
    def _polygon_total(self, polygon):
        people, covered = 0.0, 0.0
        for start in range(0, self.density.size, BLOCK ** 2):
            flat = np.arange(start, min(start + BLOCK ** 2, self.density.size))
            flat = flat[self.valid.ravel()[flat]]
            rr, cc = np.unravel_index(flat, self.shape)
            cells = shapely.box(self.x[cc], -self.y[rr + 1], self.x[cc + 1], -self.y[rr])
            touches = shapely.intersects(polygon, cells)
            area = shapely.area(shapely.intersection(polygon, cells[touches]))
            people += float(np.sum(area * self.density[rr[touches], cc[touches]]))
            covered += float(np.sum(area))
        return people, covered

    # Returns (fine index, coarse index, support area) for each valid native intersection; used by
    # model.block_inputs.
    def overlaps(self, cells):
        first_row = np.searchsorted(self.y, -cells.y, side="right") - 1
        first_col = np.searchsorted(self.x, cells.x, side="right") - 1
        height, width = self.shape
        parts = []
        # Native cells are at least one fine cell wide, so at most 2×2 candidates.
        for row_shift, col_shift in ((0, 0), (0, 1), (1, 0), (1, 1)):
            row, col = first_row + row_shift, first_col + col_shift
            idx = np.flatnonzero((row >= 0) & (col >= 0) & (row < height) & (col < width))
            idx = idx[self.valid[row[idx], col[idx]]]
            rr, cc = row[idx], col[idx]
            left = np.maximum(cells.x[idx], self.x[cc])
            right = np.minimum(cells.x[idx] + PIXEL, self.x[cc + 1])
            top = np.minimum(cells.y[idx], -self.y[rr])
            bottom = np.maximum(cells.y[idx] - PIXEL, -self.y[rr + 1])
            keep = (right > left) & (top > bottom)
            idx, rr, cc = idx[keep], rr[keep], cc[keep]
            left, right, top, bottom = left[keep], right[keep], top[keep], bottom[keep]
            area = (right - left) * (top - bottom)
            edge = ~shapely.is_missing(cells.edge[idx])
            rectangles = shapely.box(left[edge], bottom[edge], right[edge], top[edge])
            area[edge] = shapely.area(shapely.intersection(cells.edge[idx[edge]], rectangles))
            keep = area > AREA_EPS
            parts.append((idx[keep], rr[keep] * width + cc[keep], area[keep]))
        return tuple(np.concatenate(parts_axis) for parts_axis in zip(*parts))
