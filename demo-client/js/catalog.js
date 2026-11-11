// Dataset discovery and grouping. Pure functions (no DOM) so they are unit-tested in Node.
//
// Sources, merged in this order (later ones only fill gaps or add facts the earlier ones lack):
//   1. OGC API collections (standard): id, title, description, keywords, extents, links.
//   2. TiTiler /tiles/datasets (vendor): structured version, CRS, units, NoData, bbox, year for rasters.
//   3. Optional public catalog metadata (requested from Agent 5): provenance subset — license,
//      synthetic flag, logical group_id, region, resolution. Missing => shown as "not exposed".
// Nothing here knows any dataset id; grouping is driven by product_level / year / units / group_id.

export const PRODUCT_LEVELS = ["level1", "level2", "source", "other"];
const LEVEL_SET = new Set(PRODUCT_LEVELS);
const KIND_SET = new Set(["raster", "vector"]);

export const LEVEL_LABEL = {
  level1: "Level 1",
  level2: "Level 2",
  source: "Source inputs",
  other: "Other layers",
};

const hasLink = (collection, pattern) =>
  (collection.links || []).some((l) => pattern.test(l.rel || "") || pattern.test(l.href || ""));

/** Facts that the OGC publisher appends to the description: "(catalog version 1; native CRS …; units …)". */
export function parseDescriptionFacts(description = "") {
  // Source labels may themselves contain parentheses ("… model (cgis_level1) from …"), so anchor on the
  // last "(catalog version " and the final ")" rather than balancing brackets.
  const facts = {};
  const trimmed = description.trim();
  const start = trimmed.lastIndexOf("(catalog version ");
  if (start < 0 || !trimmed.endsWith(")")) return { text: trimmed, facts };
  const body = trimmed.slice(start + 1, -1);
  const m = { index: start, 1: body };
  for (const part of m[1].split(";").map((s) => s.trim())) {
    let r;
    if ((r = /^catalog version (\d+)$/.exec(part))) facts.version = Number(r[1]);
    else if ((r = /^native CRS (\S+)$/.exec(part))) facts.crs = r[1];
    else if ((r = /^units (\S+)$/.exec(part))) facts.units = r[1];
    else if ((r = /^NoData (\S+)$/.exec(part))) facts.nodata = Number(r[1]);
    else if ((r = /^source (.+)$/.exec(part))) facts.source = r[1];
  }
  return { text: trimmed.slice(0, m.index).trim(), facts };
}

/** Keywords are [kind, product_level, source, units, year] with empty entries dropped. */
export function parseKeywords(keywords = []) {
  const out = {};
  const rest = [];
  for (const raw of keywords) {
    const k = String(raw);
    if (!out.kind && KIND_SET.has(k)) out.kind = k;
    else if (!out.productLevel && LEVEL_SET.has(k)) out.productLevel = k;
    else if (!out.year && /^(1[89]|2\d)\d\d$/.test(k)) out.year = Number(k);
    else rest.push(k);
  }
  // Of the remaining words, a snake_case token with "per" or a unit-like suffix is the unit.
  const unitIdx = rest.findIndex((w) => /^[a-z0-9]+(_[a-z0-9]+)+$/.test(w) || /per_|_per|^count$|^m2/.test(w));
  if (unitIdx >= 0) out.units = rest.splice(unitIdx, 1)[0];
  if (rest.length) out.source = rest.join(" ");
  return out;
}

export function collectionBbox(c) {
  const b = c?.extent?.spatial?.bbox?.[0];
  return Array.isArray(b) && b.length >= 4 && b.every(Number.isFinite) ? b.slice(0, 4).map(Number) : null;
}

export function parseCollection(c) {
  const { text, facts } = parseDescriptionFacts(c.description || "");
  const kw = parseKeywords(c.keywords || []);
  const hasTiles = hasLink(c, /tiles/);
  const hasItems = hasLink(c, /\/items/) && !hasTiles;
  return {
    id: c.id,
    title: c.title || c.id,
    description: text,
    kind: hasTiles ? "raster" : hasItems ? "vector" : kw.kind || "other",
    productLevel: kw.productLevel || "other",
    year: kw.year ?? null,
    units: facts.units || kw.units || null,
    source: facts.source || kw.source || null,
    crs: facts.crs || null,
    nodata: facts.nodata ?? null,
    version: facts.version ?? null,
    bbox: collectionBbox(c),
    hasTiles,
    hasItems,
    resolution: null,      // [x, y] in native CRS units, filled from metadata/info when known
    license: null,
    citation: null,
    synthetic: null,       // true | false | null (unknown)
    groupId: null,
    region: null,
    metadataSource: ["ogc"],
  };
}

export function mergeVendor(ds, v) {
  if (!v) return ds;
  return {
    ...ds,
    version: ds.version ?? v.version ?? null,
    crs: ds.crs || v.crs || null,
    units: ds.units || v.units || null,
    nodata: ds.nodata ?? (v.nodata ?? null),
    year: ds.year ?? v.year ?? null,
    bbox: ds.bbox || (Array.isArray(v.bbox) ? v.bbox : null),
    metadataSource: [...ds.metadataSource, "tiles"],
  };
}

export function mergeMetadata(ds, m) {
  if (!m) return ds;
  const res = [m.resolution_x, m.resolution_y].every((r) => typeof r === "number" && r > 0)
    ? [Math.abs(m.resolution_x), Math.abs(m.resolution_y)] : ds.resolution;
  return {
    ...ds,
    productLevel: LEVEL_SET.has(m.product_level) ? m.product_level : ds.productLevel,
    year: m.year ?? ds.year,
    units: m.units || ds.units,
    crs: m.crs || ds.crs,
    nodata: m.nodata ?? ds.nodata,
    version: m.version ?? ds.version,
    source: m.source || ds.source,
    resolution: res,
    license: m.license || null,
    citation: m.citation || null,
    synthetic: typeof m.synthetic === "boolean" ? m.synthetic : ds.synthetic,
    groupId: m.group_id || null,
    region: m.region || null,
    versions: Array.isArray(m.versions) ? m.versions : undefined,
    metadataSource: [...ds.metadataSource, "catalog"],
  };
}

/** synthetic | real | unknown. Text markers are what the Phase 2 fixtures are required to carry. */
export function provenanceClass(ds) {
  if (ds.synthetic === true) return "synthetic";
  const text = `${ds.title} ${ds.source || ""} ${ds.description || ""}`;
  if (/\bSYNTHETIC\b|deterministic-fixture|\bmock\b/i.test(text)) return "synthetic";
  if (ds.synthetic === false) return "real";
  return "unknown";
}

export const PROVENANCE_LABEL = {
  synthetic: "SYNTHETIC test data",
  real: "Real data · provenance recorded",
  unknown: "Provenance not exposed",
};

function area(b) {
  return b ? Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]) : 0;
}

export function intersects(a, b) {
  return Boolean(a && b && a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]);
}

export function unionBbox(boxes) {
  const list = boxes.filter(Boolean);
  if (!list.length) return null;
  return [
    Math.min(...list.map((b) => b[0])), Math.min(...list.map((b) => b[1])),
    Math.max(...list.map((b) => b[2])), Math.max(...list.map((b) => b[3])),
  ];
}

/** "national" when the extent covers at least a quarter of the study area, else "local". */
export function scopeOf(bbox, studyBbox) {
  if (!bbox || !studyBbox) return "local";
  return area(bbox) >= 0.25 * area(studyBbox) ? "national" : "local";
}

/** Approximate ground cell size in metres for display ("≈ 92 m"). Never used for statistics. */
export function approxCellMetres(ds) {
  if (!ds.resolution) return null;
  const [rx, ry] = ds.resolution;
  const geographic = !ds.crs || /^(EPSG:4326|OGC:CRS84)$/i.test(ds.crs);
  if (!geographic) return Math.sqrt(rx * ry);
  const lat = ds.bbox ? (ds.bbox[1] + ds.bbox[3]) / 2 : 0;
  const mx = rx * 111320 * Math.cos((lat * Math.PI) / 180);
  const my = ry * 110574;
  return Math.sqrt(Math.abs(mx * my));
}

function stripYear(title, year) {
  if (!year) return title;
  return title.replace(new RegExp(`\\s*\\b${year}\\b\\s*`), " ").replace(/\s{2,}/g, " ").replace(/\s+([),.:])/g, "$1").trim();
}

const seriesKey = (d) => [d.productLevel, d.units || "", d.source || "", d.groupId || "", d.scope].join("|");

/**
 * Build the browsable catalogue.
 * Returns {datasets, rasters, vectors, byId, series, level2Groups, sections, nationalExtent}
 *  - series: rasters sharing level/units/source/group/scope with ≥2 distinct years (e.g. 11 Level 1 years).
 *  - level2Groups: Level 2 products grouped by provenance.group_id (fallback: one group per year).
 */
export function buildCatalog({ collections = [], vendor = [], metadata = [], studyBbox = null } = {}) {
  const vendorById = new Map(vendor.map((v) => [v.dataset_id, v]));
  const metaById = new Map(metadata.map((m) => [m.dataset_id, m]));
  const datasets = collections
    .filter((c) => c && typeof c.id === "string")
    .map((c) => mergeMetadata(mergeVendor(parseCollection(c), vendorById.get(c.id)), metaById.get(c.id)))
    .map((d) => ({ ...d, scope: scopeOf(d.bbox, studyBbox), provenance: null }))
    .map((d) => ({ ...d, provenance: provenanceClass(d) }));

  const rasters = datasets.filter((d) => d.kind === "raster");
  const vectors = datasets.filter((d) => d.kind === "vector");
  const byId = new Map(datasets.map((d) => [d.id, d]));

  // Level 2: logical groups, never treated as a time series.
  const l2 = rasters.filter((d) => d.productLevel === "level2");
  const level2Map = new Map();
  for (const d of l2) {
    const key = d.groupId || `level2-${d.year ?? "undated"}`;
    if (!level2Map.has(key)) level2Map.set(key, { id: key, year: d.year, units: d.units, members: [], declared: Boolean(d.groupId) });
    level2Map.get(key).members.push(d.id);
  }
  const level2Groups = [...level2Map.values()].map((g) => ({
    ...g,
    members: g.members.sort((a, b) => byId.get(a).title.localeCompare(byId.get(b).title)),
  }));

  // Time series among the remaining rasters.
  const buckets = new Map();
  for (const d of rasters) {
    if (d.productLevel === "level2" || d.year === null) continue;
    const key = seriesKey(d);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(d);
  }
  const series = [];
  for (const [key, list] of buckets) {
    const years = [...new Set(list.map((d) => d.year))].sort((a, b) => a - b);
    if (years.length < 2) continue;
    const byYear = new Map();
    for (const d of list.sort((a, b) => a.year - b.year || (b.version ?? 0) - (a.version ?? 0))) {
      if (!byYear.has(d.year)) byYear.set(d.year, d.id);
    }
    const first = byId.get(byYear.get(years[0]));
    series.push({
      key,
      title: stripYear(first.title, first.year),
      productLevel: first.productLevel,
      units: first.units,
      scope: first.scope,
      years,
      byYear,
      ids: years.map((y) => byYear.get(y)),
    });
  }
  series.sort((a, b) => PRODUCT_LEVELS.indexOf(a.productLevel) - PRODUCT_LEVELS.indexOf(b.productLevel)
    || (a.scope === b.scope ? 0 : a.scope === "national" ? -1 : 1) || a.title.localeCompare(b.title));
  for (const s of series) for (const id of s.ids) byId.get(id).seriesKey = s.key;
  for (const g of level2Groups) for (const id of g.members) byId.get(id).level2Group = g.id;

  // Ordered raster list for the layer selects: by level, then series/year, then title.
  const rasterOrder = [...rasters].sort((a, b) =>
    PRODUCT_LEVELS.indexOf(a.productLevel) - PRODUCT_LEVELS.indexOf(b.productLevel)
    || (a.scope === b.scope ? 0 : a.scope === "national" ? -1 : 1)
    || (a.seriesKey || a.id).localeCompare(b.seriesKey || b.id)
    || (a.year ?? 0) - (b.year ?? 0)
    || a.title.localeCompare(b.title));

  const nationalExtent = unionBbox(datasets.filter((d) => d.scope === "national").map((d) => d.bbox));
  return { datasets, rasters: rasterOrder, vectors, byId, series, level2Groups, nationalExtent,
    dataExtent: unionBbox(datasets.map((d) => d.bbox)) };
}

/** Text + facet filter used by the dataset browser. */
export function matchesFilter(ds, text, level) {
  if (level && level !== "all" && ds.productLevel !== level) return false;
  if (!text) return true;
  const hay = `${ds.id} ${ds.title} ${ds.year ?? ""} ${ds.source ?? ""} ${ds.units ?? ""} ${ds.productLevel}`.toLowerCase();
  return text.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
}

/** Can raw pixel values of a and b be compared cell-for-cell? */
export function pixelComparability(a, b) {
  if (!a || !b) return { ok: false, reason: "missing" };
  if (!a.units || a.units !== b.units) return { ok: false, reason: "units" };
  if (a.resolution && b.resolution) {
    const ra = a.resolution[0] * a.resolution[1];
    const rb = b.resolution[0] * b.resolution[1];
    if (Math.abs(ra - rb) / Math.max(ra, rb) > 0.01 || (a.crs && b.crs && a.crs !== b.crs)) {
      return { ok: false, reason: "resolution", ratio: Math.max(ra, rb) / Math.min(ra, rb) };
    }
    return { ok: true };
  }
  if (a.productLevel !== b.productLevel && (a.productLevel === "level2" || b.productLevel === "level2")) {
    return { ok: false, reason: "resolution", ratio: null };
  }
  return { ok: a.productLevel === b.productLevel, reason: a.productLevel === b.productLevel ? null : "level" };
}
