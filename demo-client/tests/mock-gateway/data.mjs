// Deterministic MOCK datasets for the browser tests. Every title says MOCK or SYNTHETIC: these are
// test doubles of the gateway's responses, not population estimates. Dataset ids deliberately differ
// from the real naming (india-l1-pop-YYYY) to prove the viewer discovers layers without knowing names.

export const INDIA_BBOX = [68.18, 6.75, 97.41, 35.5];

// Rough mainland outline used only to mask the mock rasters (NoData outside).
const OUTLINE = [[68.2, 23.7], [68.8, 24.3], [70.0, 24.2], [70.6, 25.7], [69.5, 27.0], [70.4, 28.0], [71.9, 27.9],
  [73.0, 29.5], [74.6, 31.1], [74.5, 32.9], [73.9, 34.2], [74.6, 35.0], [77.0, 35.5], [78.0, 35.3], [79.5, 33.0],
  [78.8, 32.3], [78.7, 31.3], [80.2, 30.4], [81.1, 30.0], [83.0, 28.9], [85.0, 27.4], [88.0, 26.4], [88.2, 27.9],
  [88.8, 27.2], [89.8, 26.7], [92.0, 26.9], [94.5, 27.7], [96.0, 28.3], [97.4, 28.2], [96.7, 27.4], [95.2, 26.6],
  [94.7, 25.0], [94.1, 23.8], [93.3, 22.0], [92.4, 21.9], [91.8, 24.2], [90.0, 25.2], [88.9, 25.8], [88.7, 24.3],
  [89.0, 22.0], [87.0, 21.5], [86.9, 20.7], [85.0, 19.4], [84.0, 18.3], [82.3, 16.6], [80.3, 15.8], [80.2, 13.4],
  [79.8, 11.0], [79.3, 10.3], [78.2, 8.9], [77.5, 8.1], [76.5, 8.9], [76.0, 10.3], [75.3, 11.9], [74.7, 13.4],
  [74.0, 15.2], [73.4, 16.9], [72.9, 19.2], [72.6, 21.1], [72.8, 21.9], [72.5, 22.3], [70.4, 20.8], [69.0, 22.4], [68.2, 23.7]];

export function pointInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function pointInGeometry(x, y, geom) {
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
  return polys.some((rings) => pointInRing(x, y, rings[0]) && !rings.slice(1).some((r) => pointInRing(x, y, r)));
}

const CITIES = [
  [77.21, 28.61, 1.0], [72.88, 19.08, 1.0], [88.36, 22.57, 0.9], [80.27, 13.08, 0.7], [77.59, 12.97, 0.7],
  [78.47, 17.39, 0.6], [72.57, 23.02, 0.5], [73.86, 18.52, 0.45], [80.95, 26.85, 0.4], [85.14, 25.59, 0.4],
  [75.79, 26.91, 0.4], [91.74, 26.14, 0.25], [78.12, 9.93, 0.25], [76.27, 9.93, 0.3], [77.41, 23.26, 0.3],
];

function nationalValue(lon, lat, year) {
  if (!pointInRing(lon, lat, OUTLINE)) return null;
  const growth = 1 + 0.018 * (year - 2001);
  const plain = 10 * Math.exp(-((lat - 26 + (lon - 80) * 0.12) ** 2) / 6);
  const texture = 4 * (1 + Math.sin(lon * 3.1) * Math.cos(lat * 2.7));
  let urban = 0;
  for (const [cx, cy, w] of CITIES) {
    const d2 = (lon - cx) ** 2 + (lat - cy) ** 2;
    urban += w * (900 * Math.exp(-d2 / (2 * 0.12 ** 2)) + 220 * Math.exp(-d2 / (2 * 0.7 ** 2)));
  }
  return Math.max(0, (6 + plain + texture + urban * (1 + 0.012 * (year - 2001))) * growth);
}

const TOWNS = [
  { slug: "pune", name: "Pune", lon: 73.86, lat: 18.52, cls: "urban" },
  { slug: "jaipur", name: "Jaipur", lon: 75.79, lat: 26.91, cls: "urban" },
  { slug: "guwahati", name: "Guwahati", lon: 91.74, lat: 26.14, cls: "urban" },
  { slug: "madurai", name: "Madurai", lon: 78.12, lat: 9.93, cls: "peri-urban" },
  { slug: "kochi-fringe", name: "Kochi fringe", lon: 76.33, lat: 10.02, cls: "peri-urban" },
  { slug: "bhagalpur-rural", name: "Bhagalpur rural block", lon: 87.0, lat: 25.25, cls: "rural" },
];

const half = 0.06;
export const townBbox = (t) => [t.lon - half, t.lat - half, t.lon + half, t.lat + half];

function level2Value(lon, lat) {
  const base = nationalValue(lon, lat, 2011);
  if (base === null) return null;
  const fine = 1 + 0.5 * Math.sin(lon * 900) * Math.sin(lat * 900);
  return Math.max(0, (base / 15.6) * fine);
}

// ------------------------------------------------------------- dataset registry

const RES_L1 = 0.000833333333;
const RES_L2 = 0.000211;

function syntheticValue(year) {
  return (lon, lat) => {
    const col = Math.floor((lon - 78.0) / 0.001);
    const row = Math.floor((18.0 - lat) / 0.001);
    if (col < 0 || row < 0 || col >= 64 || row >= 64) return undefined; // outside extent
    if (col < 4 || row < 4) return null; // NoData strip
    return year === 2001 ? 1 : 2;
  };
}

export function createRegistry({ sharedStyle = false, datasetSet = "full" } = {}) {
  const rasters = [];
  const phase2Only = datasetSet === "phase2";
  for (const year of [2001, 2011]) {
    rasters.push({
      id: `synthetic-worldpop-${year}`, version: 1, title: `SYNTHETIC population raster (${year})`,
      description: "Deterministic fixture.", product_level: "source", year, units: "people_per_cell",
      source: "deterministic-fixture", crs: "EPSG:4326", nodata: -9999, bbox: [78.0, 17.936, 78.064, 18.0],
      res: 0.001, maxzoom: 13, vendorMaxzoom: 10, synthetic: true, grid: true, value: syntheticValue(year),
    });
  }
  for (let year = 2001; year <= 2011; year++) {
    rasters.push({
      id: `mockindia-l1-${year}`, version: 1, title: `MOCK India Level 1 population ${year}`,
      description: "Mock national people-per-cell surface for UI tests (not WorldPop, not a model output).",
      product_level: "level1", year, units: "people_per_cell", source: "MOCK fixture generator", crs: "EPSG:4326",
      nodata: -99999, bbox: INDIA_BBOX, res: RES_L1, maxzoom: 10, vendorMaxzoom: 10, synthetic: true,
      license: "Test fixture (no license required)", group_id: null,
      value: (lon, lat) => nationalValue(lon, lat, year),
      style: sharedStyle ? { lo: 0, hi: 1000, shared: true } : null,
    });
  }
  for (const t of TOWNS) {
    rasters.push({
      id: `mockindia-l2-2011-${t.slug}`, version: 1, title: `MOCK Level 2 2011 – ${t.name}`,
      description: `Mock ~23.5 m target grid for ${t.name} (${t.cls}).`, product_level: "level2", year: 2011,
      units: "people_per_cell", source: "MOCK fixture generator", crs: "EPSG:4326", nodata: -99999,
      bbox: townBbox(t), res: RES_L2, maxzoom: 13, vendorMaxzoom: 12, synthetic: true, group_id: "mockindia-level2-2011",
      region: t.name, value: level2Value,
    });
  }
  for (const r of rasters) r.kind = "raster";
  if (phase2Only) {
    // Exactly what the Phase 2 stack serves after `ingestion seed`: two rasters and the towns.
    return { rasters: rasters.filter((r) => r.id.startsWith("synthetic-")), vectors: createVectors().filter((v) => v.id === "synthetic-towns"), towns: [] };
  }
  return { rasters, vectors: createVectors(), towns: TOWNS };
}

function rect(b) {
  return { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] };
}
const round = (v) => Math.round(v * 1e6) / 1e6;

function createVectors() {
  const vectors = [];
  vectors.push({
    id: "synthetic-towns", title: "SYNTHETIC towns", product_level: "source", source: "deterministic-fixture",
    bbox: [78.01, 17.95, 78.035, 17.99], features: [
      { id: "town-a", properties: { unit_id: "town-a", name: "Town A", level: "synthetic_town" }, geometry: rect([78.01, 17.98, 78.02, 17.99]) },
      { id: "town-b", properties: { unit_id: "town-b", name: "Town B", level: "synthetic_town" }, geometry: rect([78.03, 17.95, 78.035, 17.955]) },
    ],
  });
  // 12,000 district-like cells: enough to prove bbox scoping, pagination and the per-view budget.
  const cols = 120; const rows = 100;
  const [w, s, e, n] = INDIA_BBOX;
  const dx = (e - w) / cols; const dy = (n - s) / rows;
  const districts = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c + 1;
      const b = [round(w + c * dx), round(s + r * dy), round(w + (c + 1) * dx), round(s + (r + 1) * dy)];
      districts.push({ id: `md-${String(i).padStart(5, "0")}`, properties: { unit_id: `md-${i}`, name: `Mock District ${String(i).padStart(5, "0")}`, level: "district" }, geometry: rect(b), bbox: b });
    }
  }
  vectors.push({ id: "mock-india-districts", title: "MOCK India districts (12,000 cells)", product_level: "other", source: "MOCK fixture generator", bbox: INDIA_BBOX, features: districts });
  const states = [];
  const sc = 6; const sr = 6;
  for (let r = 0; r < sr; r++) {
    for (let c = 0; c < sc; c++) {
      const i = r * sc + c + 1;
      const b = [round(w + (c * (e - w)) / sc), round(s + (r * (n - s)) / sr), round(w + ((c + 1) * (e - w)) / sc), round(s + ((r + 1) * (n - s)) / sr)];
      states.push({ id: `ms-${i}`, properties: { unit_id: `ms-${i}`, name: `Mock State ${String(i).padStart(2, "0")}`, level: "state" }, geometry: rect(b), bbox: b });
    }
  }
  vectors.push({ id: "mock-india-states", title: "MOCK India states (36 cells)", product_level: "other", source: "MOCK fixture generator", bbox: INDIA_BBOX, features: states });
  vectors.push({
    id: "mock-india-towns", title: "MOCK Level 2 town boundaries", product_level: "other", source: "MOCK fixture generator",
    bbox: [73.0, 9.0, 92.5, 27.5],
    features: TOWNS.map((t) => {
      const b = townBbox(t).map((v, i) => round(v + (i < 2 ? 0.01 : -0.01)));
      return { id: `mt-${t.slug}`, properties: { unit_id: `mt-${t.slug}`, name: t.name, level: "town" }, geometry: rect(b), bbox: b };
    }),
  });
  for (const v of vectors) {
    v.kind = "vector";
    for (const f of v.features) {
      f.type = "Feature";
      if (!f.bbox) {
        const ring = f.geometry.coordinates[0];
        f.bbox = [Math.min(...ring.map((p) => p[0])), Math.min(...ring.map((p) => p[1])), Math.max(...ring.map((p) => p[0])), Math.max(...ring.map((p) => p[1]))];
      }
    }
  }
  return vectors;
}

// ------------------------------------------------------------- statistics & sampling

export function valueAt(ds, lon, lat) {
  if (lon < ds.bbox[0] || lon > ds.bbox[2] || lat < ds.bbox[1] || lat > ds.bbox[3]) return undefined;
  if (ds.grid || ds.raster) {
    if (ds.raster) {
      const col = Math.floor((lon - ds.raster.originX) / ds.raster.resX);
      const row = Math.floor((ds.raster.originY - lat) / ds.raster.resY);
      if (col < 0 || row < 0 || col >= ds.raster.width || row >= ds.raster.height) return undefined;
      const v = ds.raster.values[row * ds.raster.width + col];
      return v === ds.nodata || !Number.isFinite(v) ? null : v;
    }
  }
  return ds.value(lon, lat);
}

export function statistics(ds) {
  if (ds._stats) return ds._stats;
  const vals = [];
  const N = 220;
  const [w, s, e, n] = ds.bbox;
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      const v = valueAt(ds, w + ((i + 0.5) * (e - w)) / N, s + ((j + 0.5) * (n - s)) / N);
      if (v !== null && v !== undefined) vals.push(v);
    }
  }
  vals.sort((a, b) => a - b);
  const q = (p) => vals[Math.min(vals.length - 1, Math.floor((p / 100) * vals.length))];
  const sum = vals.reduce((a, b) => a + b, 0);
  ds._stats = { b1: { min: vals[0] ?? 0, max: vals.at(-1) ?? 0, mean: vals.length ? sum / vals.length : 0, count: vals.length, percentile_2: q(2) ?? 0, percentile_98: q(98) ?? 0, valid_percent: (vals.length / (N * N)) * 100 } };
  return ds._stats;
}

export function styleOf(ds) {
  if (ds.style) return ds.style;
  const st = statistics(ds).b1;
  const lo = Math.min(0, st.min);
  let hi = st.max;
  if (!(hi > lo)) hi = lo + 1;
  return { lo, hi, shared: false };
}

/** Zonal statistics with the pixel-centre rule; exact for small windows, sampled for large ones. */
export function zonal(ds, aoi) {
  const xs = []; const ys = [];
  const walk = (c) => { if (typeof c[0] === "number") { xs.push(c[0]); ys.push(c[1]); } else c.forEach(walk); };
  walk(aoi.coordinates);
  const box = [Math.max(Math.min(...xs), ds.bbox[0]), Math.max(Math.min(...ys), ds.bbox[1]), Math.min(Math.max(...xs), ds.bbox[2]), Math.min(Math.max(...ys), ds.bbox[3])];
  if (!(box[0] < box[2] && box[1] < box[3])) return { error: { status: 422, code: "AOI_OUTSIDE_RASTER", message: "the AOI does not overlap the dataset's raster extent" } };
  const res = ds.res;
  const origin = ds.raster ? [ds.raster.originX, ds.raster.originY] : [ds.bbox[0], ds.bbox[3]];
  const c0 = Math.floor((box[0] - origin[0]) / res); const c1 = Math.ceil((box[2] - origin[0]) / res);
  const r0 = Math.floor((origin[1] - box[3]) / res); const r1 = Math.ceil((origin[1] - box[1]) / res);
  const windowPixels = (c1 - c0) * (r1 - r0);
  if (windowPixels > 25_000_000) return { error: { status: 413, code: "AOI_TOO_LARGE", message: `the AOI window has ${windowPixels} pixels; the limit is 25000000` } };
  const step = Math.max(1, Math.ceil(Math.max(c1 - c0, r1 - r0) / 400));
  let sum = 0; let count = 0; let inside = 0;
  for (let r = r0; r < r1; r += step) {
    for (let c = c0; c < c1; c += step) {
      const lon = origin[0] + (c + 0.5) * res;
      const lat = origin[1] - (r + 0.5) * res;
      if (!pointInGeometry(lon, lat, aoi)) continue;
      const w = Math.min(step, c1 - c) * Math.min(step, r1 - r);
      const v = valueAt(ds, lon, lat);
      if (v === undefined) continue;
      inside += w;
      if (v === null) continue;
      sum += v * w; count += w;
    }
  }
  if (!inside) return { error: { status: 422, code: "NO_PIXELS_SELECTED", message: "no pixel centre falls inside the AOI" } };
  return { count_valid: count, sum: count ? (ds.grid ? Math.round(sum) : sum) : null, mean: count ? sum / count : null, pixels_in_aoi: inside, nodata_excluded: inside - count, sampled: step > 1 };
}
