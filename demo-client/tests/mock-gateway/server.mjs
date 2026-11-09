// Mock CyberGIS gateway for the viewer's browser tests (Agent 6). Node >= 18, no dependencies.
//
//   node mock-gateway/server.mjs [--port 8099] [--app-api on|off] [--shared-style] [--like on|off]
//
// Serves demo-client/ at /viewer/ and test doubles of /ogc (OGC API Features/Tiles shapes as served by
// pygeoapi), /tiles (TiTiler-shaped vendor routes), /api (Phase 2 processing API + the provisional
// Agent 5 /api/exports), /auth and /admin (provisional Agent 5 contract, docs/openapi.agent5.provisional.json).
// Everything here is MOCK/SYNTHETIC test data. Test hooks live under /__mock/.

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { encodePng, encodeGeoTiff, decodeGeoTiff } from "./encoders.mjs";
import { createRegistry, statistics, styleOf, zonal, valueAt, pointInGeometry } from "./data.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_ROOT = path.resolve(here, "..", "..");

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(`--${name}`); return i >= 0 ? (args[i + 1] && !args[i + 1].startsWith("--") ? args[i + 1] : true) : def; };
const PORT = Number(opt("port", process.env.MOCK_PORT || 8099));
const APP_API = opt("app-api", "on") !== "off";
const LIKE = opt("like", "on") !== "off";
const SHARED_STYLE = Boolean(opt("shared-style", false));
const DATASET_SET = opt("dataset-set", "full");
const SESSION_TTL_MS = Number(process.env.MOCK_SESSION_TTL_MS || 30 * 60 * 1000);
// Fixture accounts for the mock only (no real system uses these).
const USERS = {
  admin: { password: process.env.MOCK_ADMIN_PASSWORD || "admin-fixture-pass", role: "admin" },
  analyst: { password: process.env.MOCK_USER_PASSWORD || "user-fixture-pass", role: "user" },
};

let registry;
let state;
function reset() {
  registry = createRegistry({ sharedStyle: SHARED_STYLE, datasetSet: DATASET_SET });
  state = {
    sessions: new Map(), failures: new Map(), jobs: new Map(), exports: new Map(), adminJobs: new Map(),
    versions: new Map(), // dataset_id -> [{version, status, ...}]
    requests: [], delayMs: 0, failNext: null,
  };
  for (const d of [...registry.rasters, ...registry.vectors]) {
    state.versions.set(d.id, [{ version: d.version || 1, status: "ready", crs: "EPSG:4326", checksum_sha256: crypto.createHash("sha256").update(d.id).digest("hex"),
      size_bytes: 1024 * 1024, created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z", created_by: "seed",
      provenance: { synthetic: true, source: d.source, group_id: d.group_id || undefined }, validation: { ok: true, errors: [], warnings: [] } }]);
  }
}
reset();

const allDatasets = () => [...registry.rasters, ...registry.vectors];
const findRaster = (id) => registry.rasters.find((d) => d.id === id);
const findVector = (id) => registry.vectors.find((d) => d.id === id);

// ------------------------------------------------------------- helpers

function send(res, status, body, headers = {}) {
  const isBuf = Buffer.isBuffer(body);
  const payload = isBuf ? body : typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(status, { "Content-Type": isBuf ? "application/octet-stream" : "application/json", "Cache-Control": "no-store", ...headers });
  res.end(payload);
}
const err = (res, status, code, message, details) => send(res, status, { error: { code, message, ...(details ? { details } : {}) } });

function readBody(req, limit = 50 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = []; let size = 0;
    req.on("data", (c) => { size += c.length; if (size > limit) { reject(Object.assign(new Error("too large"), { code: "TOO_LARGE" })); req.destroy(); } else chunks.push(c); });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
async function readJson(req) {
  const buf = await readBody(req, 2_000_000);
  try { return JSON.parse(buf.toString("utf8") || "{}"); } catch { return null; }
}

function cookies(req) {
  return Object.fromEntries((req.headers.cookie || "").split(";").map((c) => c.trim().split("=")).filter((p) => p[0]));
}
function session(req) {
  const token = cookies(req).cgis_session || (req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  const s = token && state.sessions.get(token);
  if (!s) return null;
  if (s.expires < Date.now()) { state.sessions.delete(token); return { expired: true }; }
  return { ...s, token };
}
function requireUser(req, res, { admin = false, mutating = false } = {}) {
  const s = session(req);
  if (!s) { err(res, 401, "UNAUTHENTICATED", "sign in required"); return null; }
  if (s.expired) { err(res, 401, "SESSION_EXPIRED", "the session has expired"); return null; }
  if (admin && s.role !== "admin") { err(res, 403, "FORBIDDEN", "administrator role required"); return null; }
  if (mutating && req.headers["x-csrf-token"] !== s.csrf) { err(res, 403, "CSRF_FAILED", "missing or wrong X-CSRF-Token"); return null; }
  return s;
}

const base = (req) => `http://${req.headers.host}`;
const intersects = (a, b) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];

// ------------------------------------------------------------- tiles

const VIRIDIS = [[68, 1, 84], [71, 45, 123], [59, 82, 139], [44, 114, 142], [33, 145, 140], [40, 174, 128], [94, 201, 98], [173, 220, 48], [253, 231, 37]];
function colour(t) {
  const x = Math.min(1, Math.max(0, t)) * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(x)); const f = x - i;
  return VIRIDIS[i].map((c, k) => Math.round(c + (VIRIDIS[i + 1][k] - c) * f));
}
function tileBounds(z, x, y) {
  const n = 2 ** z;
  const lon = (x / n) * 360 - 180;
  const lat = (yy) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * yy) / n))) * 180) / Math.PI;
  return [lon, lat(y + 1), ((x + 1) / n) * 360 - 180, lat(y)];
}
function renderTile(ds, z, x, y, lo, hi) {
  const [w, s, e, n] = tileBounds(z, x, y);
  const N = 2 ** z;
  const px = new Uint8Array(256 * 256 * 4);
  for (let j = 0; j < 256; j++) {
    const yy = y + (j + 0.5) / 256;
    const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * yy) / N))) * 180) / Math.PI;
    for (let i = 0; i < 256; i++) {
      const lon = w + ((i + 0.5) / 256) * (e - w);
      const v = valueAt(ds, lon, lat);
      if (v === null || v === undefined) continue;
      const [r, g, b] = colour((v - lo) / (hi - lo));
      const k = (j * 256 + i) * 4;
      px[k] = r; px[k + 1] = g; px[k + 2] = b; px[k + 3] = 255;
    }
  }
  void s; void n;
  return encodePng(256, 256, px);
}
function tileRange(bbox, z) {
  const n = 2 ** z;
  const col = (lon) => Math.min(n - 1, Math.max(0, Math.floor(((lon + 180) / 360) * n)));
  const row = (lat) => {
    const r = (lat * Math.PI) / 180;
    return Math.min(n - 1, Math.max(0, Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n)));
  };
  return { minTileCol: col(bbox[0]), maxTileCol: col(bbox[2]), minTileRow: row(bbox[3]), maxTileRow: row(bbox[1]) };
}

// ------------------------------------------------------------- OGC API

function collectionJson(req, d) {
  const facts = [`catalog version ${d.version || 1}`, `native CRS ${d.crs || "EPSG:4326"}`];
  if (d.units) facts.push(`units ${d.units}`);
  if (d.nodata !== undefined && d.nodata !== null) facts.push(`NoData ${d.nodata}`);
  if (d.source) facts.push(`source ${d.source}`);
  const self = `${base(req)}/ogc/collections/${d.id}`;
  const links = [{ rel: "self", type: "application/json", href: `${self}?f=json` }];
  if (d.kind === "raster") {
    links.push({ rel: "http://www.opengis.net/def/rel/ogc/1.0/tilesets-map", type: "application/json", href: `${self}/tiles?f=json` });
    links.push({ rel: "alternate", type: "application/json", title: "TiTiler TileJSON (vendor-specific, not an OGC API resource)", href: `${base(req)}/tiles/datasets/${d.id}/WebMercatorQuad/tilejson.json` });
  } else {
    links.push({ rel: "items", type: "application/geo+json", href: `${self}/items?f=json` });
  }
  return {
    id: d.id, title: d.title, description: `${d.description || d.title} (${facts.join("; ")})`,
    keywords: [d.kind, d.product_level, d.source, d.units, d.year ? String(d.year) : null].filter(Boolean),
    links, extent: { spatial: { bbox: [d.bbox], crs: "http://www.opengis.net/def/crs/OGC/1.3/CRS84" } }, itemType: d.kind === "vector" ? "feature" : undefined,
  };
}

function tilesetJson(req, d) {
  const st = styleOf(d);
  const limits = [];
  for (let z = 0; z <= d.maxzoom; z++) limits.push({ tileMatrix: String(z), ...tileRange(d.bbox, z) });
  return {
    title: `${d.title} (WebMercatorQuad)`, dataType: "map", crs: "http://www.opengis.net/def/crs/EPSG/0/3857",
    boundingBox: { lowerLeft: [d.bbox[0], d.bbox[1]], upperRight: [d.bbox[2], d.bbox[3]], crs: "http://www.opengis.net/def/crs/OGC/1.3/CRS84" },
    tileMatrixSetLimits: limits,
    style: { id: "default", title: `viridis linear ${+st.lo.toPrecision(6)}..${+st.hi.toPrecision(6)}`,
      description: `Cell values map linearly onto the viridis colour ramp (values outside are clamped); NoData cells are transparent. Range: ${st.shared ? "configured shared range" : "dataset range (min(0, minimum) to maximum); not comparable across datasets"}.` },
    links: [{ rel: "item", templated: true, href: `${base(req)}/ogc/collections/${d.id}/tiles/WebMercatorQuad/{tileMatrix}/{tileRow}/{tileCol}?f=png` }],
  };
}

function itemsQuery(req, res, v, url) {
  const q = url.searchParams;
  let feats = v.features;
  if (q.has("bbox")) {
    const b = q.get("bbox").split(",").map(Number);
    if (b.length !== 4 || b.some((x) => !Number.isFinite(x))) return err(res, 400, "InvalidParameterValue", "bad bbox");
    feats = feats.filter((f) => intersects(f.bbox, b));
  }
  if (q.has("name")) feats = feats.filter((f) => f.properties.name === q.get("name"));
  if (q.has("filter")) {
    if (!LIKE) return err(res, 400, "InvalidParameterValue", "LIKE is not supported");
    const m = /^name LIKE '((?:[^']|'')*)%'$/.exec(q.get("filter"));
    if (!m) return err(res, 400, "InvalidParameterValue", "unsupported filter");
    const prefix = m[1].replaceAll("''", "'").toLowerCase();
    feats = feats.filter((f) => f.properties.name.toLowerCase().startsWith(prefix));
  }
  const limit = Math.min(1000, Math.max(1, Number(q.get("limit") || 10)));
  const offset = Math.max(0, Number(q.get("offset") || 0));
  const page = feats.slice(offset, offset + limit);
  const links = [{ rel: "self", href: `${base(req)}${url.pathname}${url.search}` }];
  if (offset + limit < feats.length) {
    const next = new URLSearchParams(q); next.set("offset", String(offset + limit)); next.set("limit", String(limit));
    links.push({ rel: "next", type: "application/geo+json", href: `${base(req)}${url.pathname}?${next}` });
  }
  return send(res, 200, { type: "FeatureCollection", features: page.map(({ bbox, ...f }) => f), numberMatched: feats.length, numberReturned: page.length, links }, { "Content-Type": "application/geo+json" });
}

function ogc(req, res, url, parts) {
  // parts: ["ogc", ...]
  if (parts.length === 1) return send(res, 200, { title: "MOCK OGC API", links: [] });
  if (parts[1] === "conformance") {
    return send(res, 200, { conformsTo: ["http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core", "http://www.opengis.net/spec/ogcapi-tiles-1/1.0/conf/core",
      ...(LIKE ? ["http://www.opengis.net/spec/cql2/1.0/conf/advanced-comparison-operators"] : [])] });
  }
  if (parts[1] !== "collections") return err(res, 404, "NotFound", "not found");
  if (parts.length === 2) return send(res, 200, { collections: allDatasets().filter((d) => d.status !== "hidden").sort((a, b) => a.id.localeCompare(b.id)).map((d) => collectionJson(req, d)), links: [] });
  const id = parts[2];
  const d = allDatasets().find((x) => x.id === id);
  if (!d) return err(res, 404, "NotFound", "collection not found");
  if (parts.length === 3) return send(res, 200, collectionJson(req, d));
  if (parts[3] === "items" && d.kind === "vector") {
    if (parts.length === 4) return itemsQuery(req, res, d, url);
    const f = d.features.find((x) => x.id === parts[4]);
    return f ? send(res, 200, (({ bbox, ...rest }) => rest)(f)) : err(res, 404, "NotFound", "feature not found");
  }
  if (parts[3] === "tiles" && d.kind === "raster") {
    if (parts.length === 5 && parts[4] === "WebMercatorQuad") return send(res, 200, tilesetJson(req, d));
    if (parts.length === 8 && parts[4] === "WebMercatorQuad") {
      const [z, row, col] = parts.slice(5).map(Number);
      if (z > d.maxzoom) return err(res, 404, "NotFound", "tile matrix outside limits");
      const r = tileRange(d.bbox, z);
      if (row < r.minTileRow || row > r.maxTileRow || col < r.minTileCol || col > r.maxTileCol) return err(res, 404, "NotFound", "tile outside limits");
      const st = styleOf(d);
      return send(res, 200, renderTile(d, z, col, row, st.lo, st.hi), { "Content-Type": "image/png" });
    }
  }
  return err(res, 404, "NotFound", "not found");
}

// ------------------------------------------------------------- vendor tiles

function vendor(req, res, url, parts) {
  if (parts[1] !== "datasets") return err(res, 404, "NOT_FOUND", "not found");
  if (parts.length === 2) return send(res, 200, { note: "Vendor-specific TiTiler discovery; not an OGC API resource.",
    datasets: registry.rasters.map((d) => ({ dataset_id: d.id, version: d.version, title: d.title, year: d.year, crs: d.crs, units: d.units, nodata: d.nodata, bbox: d.bbox, links: [] })) });
  const d = findRaster(parts[2]);
  if (!d) return err(res, 404, "DATASET_NOT_FOUND", "dataset not found");
  const rest = parts.slice(3);
  if (rest[0] === "info") {
    return send(res, 200, { bounds: d.bbox, crs: "http://www.opengis.net/def/crs/EPSG/0/4326", width: Math.round((d.bbox[2] - d.bbox[0]) / d.res), height: Math.round((d.bbox[3] - d.bbox[1]) / d.res), dtype: "float32", nodata_type: "Nodata", nodata_value: d.nodata, count: 1 });
  }
  if (rest[0] === "statistics") return send(res, 200, statistics(d));
  if (rest[0] === "point") {
    const [lon, lat] = rest[1].split(",").map(Number);
    const v = valueAt(d, lon, lat);
    if (v === undefined) return err(res, 404, "POINT_OUTSIDE_BOUNDS", "point outside the dataset bounds");
    return send(res, 200, { coordinates: [lon, lat], values: [v === null ? d.nodata : +v.toFixed(4)], band_names: ["b1"] });
  }
  if (rest[0] === "WebMercatorQuad" && rest[1] === "tilejson.json") {
    return send(res, 200, { tilejson: "2.2.0", tiles: [`${base(req)}/tiles/datasets/${d.id}/tiles/WebMercatorQuad/{z}/{x}/{y}.png`], minzoom: 0, maxzoom: d.vendorMaxzoom, bounds: d.bbox });
  }
  if (rest[0] === "tiles" && rest[1] === "WebMercatorQuad" && rest.length === 5) {
    const z = Number(rest[2]); const x = Number(rest[3]); const y = Number(rest[4].replace(/\.png$/, ""));
    const rescale = (url.searchParams.get("rescale") || "").split(",").map(Number);
    const st = styleOf(d);
    const [lo, hi] = rescale.length === 2 && rescale.every(Number.isFinite) ? rescale : [st.lo, st.hi];
    return send(res, 200, renderTile(d, z, x, y, lo, hi), { "Content-Type": "image/png" });
  }
  return err(res, 404, "NOT_FOUND", "not found");
}

// ------------------------------------------------------------- processing API (Phase 2 shapes)

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
function validateAoi(body) {
  if (!body || typeof body !== "object") return ["INVALID_REQUEST", "body must be JSON"];
  if (!ID_RE.test(body.dataset_id || "")) return ["INVALID_DATASET_ID", "bad dataset_id"];
  if (!body.aoi_crs) return ["INVALID_REQUEST", "aoi_crs is required"];
  const g = body.aoi && body.aoi.type === "Feature" ? body.aoi.geometry : body.aoi;
  if (!g || !["Polygon", "MultiPolygon"].includes(g.type)) return ["UNSUPPORTED_GEOMETRY_TYPE", "aoi must be a Polygon or MultiPolygon"];
  return null;
}

function processing(req, res, url, parts, body) {
  const op = parts[2];
  if (parts[1] === "operations" && (op === "zonal-statistics" || op === "clip") && req.method === "POST") {
    const bad = validateAoi(body);
    if (bad) return err(res, 422, bad[0], bad[1]);
    const d = findRaster(body.dataset_id);
    if (!d) return err(res, 404, "DATASET_NOT_FOUND", "dataset not found");
    const aoi = body.aoi.type === "Feature" ? body.aoi.geometry : body.aoi;
    const z = zonal(d, aoi);
    if (z.error) return err(res, z.error.status, z.error.code, z.error.message);
    const jobId = crypto.randomUUID();
    const common = { dataset_id: d.id, dataset_version: d.version || 1 };
    if (op === "zonal-statistics") {
      const out = { job_id: jobId, status: "completed", ...common, count_valid: z.count_valid, sum: z.sum, mean: z.mean, units: d.units,
        sum_semantics: "total_count", pixels_in_aoi: z.pixels_in_aoi, nodata_excluded: z.nodata_excluded, nodata_value: d.nodata,
        aoi_crs: body.aoi_crs, raster_crs: d.crs, pixel_selection: { rule: "pixel_center", all_touched: false },
        operations: body.operations || ["sum", "mean", "count"], warnings: z.sampled ? ["MOCK: large window sampled, totals approximate"] : [],
        dataset: { title: d.title, provenance: { synthetic: true } }, computed_at: new Date().toISOString() };
      state.jobs.set(jobId, { kind: "zonal-statistics", out });
      return send(res, 200, out);
    }
    const fmt = body.format || "GTiff";
    if (!["GTiff", "COG"].includes(fmt)) return err(res, 422, "UNSUPPORTED_FORMAT", "format must be GTiff or COG");
    const result = buildRasterExport(d, aoi, body.output_crs);
    if (result.error) return err(res, result.error.status, result.error.code, result.error.message);
    const filename = `clip_${d.id}_v${d.version || 1}_${jobId.replaceAll("-", "").slice(0, 12)}.tif`;
    const job = { job_id: jobId, kind: "clip", status: "completed", created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      input: { ...common, aoi: body.aoi, aoi_crs: body.aoi_crs, output_crs: result.crs, format: fmt },
      result: { filename, media_type: "image/tiff; application=geotiff", pixels_in_aoi: z.pixels_in_aoi, count_valid: z.count_valid, nodata_excluded: z.nodata_excluded,
        output: { width: result.width, height: result.height, crs: result.crs, nodata: d.nodata, dtype: "float32", resampled: result.resampled, size_bytes: result.bytes.length, format: fmt },
        warnings: result.resampled ? ["output was reprojected with nearest-neighbour resampling; totals of count rasters are not preserved"] : [],
        download_url: `/api/downloads/${jobId}` },
      error: null, links: { self: `/api/operations/${jobId}`, download: `/api/downloads/${jobId}` } };
    state.jobs.set(jobId, { kind: "clip", out: job, file: result.bytes, filename });
    return send(res, 200, job);
  }
  if (parts[1] === "operations" && parts.length === 3 && req.method === "GET") {
    const j = state.jobs.get(op);
    return j ? send(res, 200, j.out) : err(res, 404, "JOB_NOT_FOUND", "unknown job");
  }
  if (parts[1] === "downloads" && parts.length === 3) {
    const j = state.jobs.get(parts[2]);
    if (!j) return err(res, 404, "JOB_NOT_FOUND", "unknown job");
    if (!j.file) return err(res, 409, "NO_DOWNLOAD", "not a clip job");
    return send(res, 200, j.file, { "Content-Type": "image/tiff; application=geotiff", "Content-Disposition": `attachment; filename="${j.filename}"` });
  }
  return null;
}

function buildRasterExport(d, aoi, outputCrs) {
  const crs = outputCrs || "EPSG:4326";
  if (!["EPSG:4326", "EPSG:3857"].includes(crs)) return { error: { status: 422, code: "UNSUPPORTED_CRS", message: "MOCK gateway supports EPSG:4326 and EPSG:3857 only" } };
  const xs = []; const ys = [];
  const walk = (c) => { if (typeof c[0] === "number") { xs.push(c[0]); ys.push(c[1]); } else c.forEach(walk); };
  walk((aoi || { coordinates: [[[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]]] }).coordinates);
  const box = aoi ? [Math.max(Math.min(...xs), d.bbox[0]), Math.max(Math.min(...ys), d.bbox[1]), Math.min(Math.max(...xs), d.bbox[2]), Math.min(Math.max(...ys), d.bbox[3])] : d.bbox;
  let res = d.res;
  let w = Math.round((box[2] - box[0]) / res); let h = Math.round((box[3] - box[1]) / res);
  // MOCK: cap the written grid so a "full" national export stays small; the real service streams the COG.
  const cap = 600;
  if (w > cap || h > cap) { const k = Math.max(w, h) / cap; res *= k; w = Math.round(w / k); h = Math.round(h / k); }
  if (w < 1 || h < 1) return { error: { status: 422, code: "AOI_OUTSIDE_RASTER", message: "no overlap" } };
  const values = new Float32Array(w * h);
  if (crs === "EPSG:4326") {
    for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) {
      const lon = box[0] + (c + 0.5) * res; const lat = box[3] - (r + 0.5) * res;
      const v = aoi && !pointInGeometry(lon, lat, aoi) ? null : valueAt(d, lon, lat);
      values[r * w + c] = v === null || v === undefined ? d.nodata : v;
    }
    return { bytes: encodeGeoTiff(w, h, values, { originX: box[0], originY: box[3], resX: res, resY: res, epsg: 4326, nodata: d.nodata }), width: w, height: h, crs, resampled: res !== d.res };
  }
  const R = 6378137;
  const mx = (lon) => (lon * Math.PI * R) / 180;
  const my = (lat) => R * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
  const x0 = mx(box[0]); const y1 = my(box[3]); const x1 = mx(box[2]); const y0 = my(box[1]);
  const mres = (x1 - x0) / w;
  const mh = Math.max(1, Math.round((y1 - y0) / mres));
  const vals = new Float32Array(w * mh);
  for (let r = 0; r < mh; r++) for (let c = 0; c < w; c++) {
    const x = x0 + (c + 0.5) * mres; const y = y1 - (r + 0.5) * mres;
    const lon = (x / R) * (180 / Math.PI); const lat = (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * (180 / Math.PI);
    const v = aoi && !pointInGeometry(lon, lat, aoi) ? null : valueAt(d, lon, lat);
    vals[r * w + c] = v === null || v === undefined ? d.nodata : v;
  }
  return { bytes: encodeGeoTiff(w, mh, vals, { originX: x0, originY: y1, resX: mres, resY: mres, epsg: 3857, nodata: d.nodata }), width: w, height: mh, crs, resampled: true };
}

// ------------------------------------------------------------- auth

function auth(req, res, parts, body) {
  const action = parts[1];
  if (action === "login" && req.method === "POST") {
    const { username, password } = body || {};
    const key = String(username || "");
    const fails = (state.failures.get(key) || []).filter((t) => t > Date.now() - 60_000);
    if (fails.length >= 5) { res.setHeader("Retry-After", "60"); return err(res, 429, "RATE_LIMITED", "too many attempts"); }
    const u = USERS[key];
    if (!u || u.password !== password) { state.failures.set(key, [...fails, Date.now()]); return err(res, 401, "INVALID_CREDENTIALS", "invalid username or password"); }
    const token = crypto.randomBytes(24).toString("hex");
    const csrf = crypto.randomBytes(16).toString("hex");
    const expires = Date.now() + SESSION_TTL_MS;
    state.sessions.set(token, { user: key, role: u.role, csrf, expires });
    res.setHeader("Set-Cookie", `cgis_session=${token}; HttpOnly; SameSite=Strict; Path=/`);
    return send(res, 200, { authenticated: true, user: { username: key, role: u.role }, expires_at: new Date(expires).toISOString(), csrf_token: csrf });
  }
  if (action === "logout" && req.method === "POST") {
    const s = session(req);
    if (s && s.token) state.sessions.delete(s.token);
    res.setHeader("Set-Cookie", "cgis_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0");
    res.writeHead(204); return res.end();
  }
  if (action === "me" && req.method === "GET") {
    const s = session(req);
    if (!s || s.expired) return send(res, 200, { authenticated: false, user: null });
    return send(res, 200, { authenticated: true, user: { username: s.user, role: s.role }, expires_at: new Date(s.expires).toISOString(), csrf_token: s.csrf });
  }
  return err(res, 404, "NOT_FOUND", "not found");
}

// ------------------------------------------------------------- admin

function datasetSummary(id) {
  const d = allDatasets().find((x) => x.id === id);
  const versions = state.versions.get(id) || [];
  const latest = versions.reduce((a, b) => (b.version > a.version ? b : a), versions[0]);
  const ready = versions.filter((v) => v.status === "ready").map((v) => v.version);
  const meta = d || latest?.meta || {};
  return { dataset_id: id, title: meta.title || id, kind: meta.kind, product_level: meta.product_level, year: meta.year ?? null, units: meta.units ?? null,
    current_version: ready.length ? Math.max(...ready) : null, latest_version: latest?.version, latest_status: latest?.status, versions_count: versions.length,
    updated_at: latest?.updated_at };
}

function parseMultipart(buf, contentType) {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType || "");
  if (!m) return null;
  const boundary = Buffer.from(`--${m[1] || m[2]}`);
  const parts = {};
  let pos = buf.indexOf(boundary);
  while (pos !== -1) {
    const next = buf.indexOf(boundary, pos + boundary.length);
    if (next === -1) break;
    const part = buf.subarray(pos + boundary.length + 2, next - 2);
    const headerEnd = part.indexOf("\r\n\r\n");
    const head = part.subarray(0, headerEnd).toString("utf8");
    const content = part.subarray(headerEnd + 4);
    const name = /name="([^"]+)"/.exec(head)?.[1];
    const filename = /filename="([^"]*)"/.exec(head)?.[1];
    if (name) parts[name] = filename !== undefined ? { filename, content } : content.toString("utf8");
    pos = next;
  }
  return parts;
}

function startAdminJob(kind, datasetId, version, work) {
  const job = { job_id: crypto.randomUUID(), kind, status: "queued", progress: 0, dataset_id: datasetId, version, result: null, error: null, links: {} };
  job.links.self = `/admin/jobs/${job.job_id}`;
  state.adminJobs.set(job.job_id, job);
  setTimeout(() => { job.status = "running"; job.progress = 0.5; }, 150);
  setTimeout(() => {
    try { job.result = work(); job.status = "completed"; job.progress = 1; } catch (e) { job.status = "failed"; job.error = { code: e.code || "FAILED", message: e.message }; }
  }, 600 + state.delayMs);
  return job;
}

function validateVersion(v) {
  const errors = [];
  if (v.meta.kind === "raster") {
    try {
      const t = decodeGeoTiff(v.file);
      if (!t.epsg) errors.push({ code: "missing_crs", message: "the GeoTIFF has no EPSG-identifiable CRS" });
      if (t.nodata === null) errors.push({ code: "missing_nodata", message: "NoData is not declared" });
      if (!t.values) errors.push({ code: "unsupported_layout", message: "MOCK reads only uncompressed single-strip GeoTIFFs" });
      v.raster = t;
    } catch (e) {
      errors.push({ code: "corrupt_raster", message: `GDAL could not read the file (${e.message})` });
    }
  } else {
    try {
      const fc = JSON.parse(v.file.toString("utf8"));
      if (fc.type !== "FeatureCollection" || !fc.features?.length) errors.push({ code: "empty_collection", message: "need a FeatureCollection with at least one feature" });
      v.fc = fc;
    } catch { errors.push({ code: "invalid_json", message: "not valid GeoJSON" }); }
  }
  v.validation = { ok: !errors.length, errors, warnings: [] };
  v.status = errors.length ? "failed" : "validated";
  v.updated_at = new Date().toISOString();
  if (errors.length) throw Object.assign(new Error(errors[0].message), { code: "VALIDATION_FAILED" });
  return { validation: v.validation };
}

function publishVersion(id, v) {
  const m = v.meta;
  const existing = allDatasets().find((d) => d.id === id);
  if (m.kind === "raster") {
    const t = v.raster;
    const bbox = [t.originX, t.originY - t.resY * t.height, t.originX + t.resX * t.width, t.originY];
    const d = { id, version: v.version, title: m.title, description: m.description, product_level: m.product_level, year: m.year, units: m.units,
      source: m.source, crs: `EPSG:${t.epsg}`, nodata: t.nodata, bbox, res: t.resX, maxzoom: 14, vendorMaxzoom: 12, kind: "raster",
      raster: { originX: t.originX, originY: t.originY, resX: t.resX, resY: t.resY, width: t.width, height: t.height, values: t.values }, group_id: m.provenance?.group_id };
    if (existing) Object.assign(existing, d, { _stats: undefined }); else registry.rasters.push(d);
  } else {
    const features = v.fc.features.map((f, i) => {
      const ring = (f.geometry.type === "Polygon" ? f.geometry.coordinates : f.geometry.coordinates[0])[0];
      return { type: "Feature", id: f.id ?? `f-${i + 1}`, properties: { name: f.properties?.name ?? `Feature ${i + 1}`, level: f.properties?.level ?? null }, geometry: f.geometry,
        bbox: [Math.min(...ring.map((p) => p[0])), Math.min(...ring.map((p) => p[1])), Math.max(...ring.map((p) => p[0])), Math.max(...ring.map((p) => p[1]))] };
    });
    const bbox = [Math.min(...features.map((f) => f.bbox[0])), Math.min(...features.map((f) => f.bbox[1])), Math.max(...features.map((f) => f.bbox[2])), Math.max(...features.map((f) => f.bbox[3]))];
    const d = { id, version: v.version, title: m.title, product_level: m.product_level, source: m.source, kind: "vector", bbox, features };
    if (existing) Object.assign(existing, d); else registry.vectors.push(d);
  }
  v.status = "ready";
  v.updated_at = new Date().toISOString();
  return { published: true };
}

async function admin(req, res, url, parts) {
  const mutating = req.method === "POST";
  const s = requireUser(req, res, { admin: true, mutating });
  if (!s) return;
  if (parts[1] === "datasets" && parts.length === 2 && req.method === "GET") {
    const q = (url.searchParams.get("q") || "").toLowerCase();
    const status = url.searchParams.get("status") || "";
    let items = [...state.versions.keys()].map(datasetSummary);
    if (q) items = items.filter((d) => `${d.dataset_id} ${d.title}`.toLowerCase().includes(q));
    if (status) items = items.filter((d) => (state.versions.get(d.dataset_id) || []).some((v) => v.status === status));
    items.sort((a, b) => a.dataset_id.localeCompare(b.dataset_id));
    const offset = Number(url.searchParams.get("offset") || 0); const limit = Math.min(200, Number(url.searchParams.get("limit") || 50));
    return send(res, 200, { items: items.slice(offset, offset + limit), total: items.length, offset, limit });
  }
  if (parts[1] === "datasets" && parts[2] === "uploads" && req.method === "POST") {
    let buf;
    try { buf = await readBody(req, 20 * 1024 * 1024); } catch { return err(res, 413, "FILE_TOO_LARGE", "the file exceeds the 20 MB mock limit"); }
    const form = parseMultipart(buf, req.headers["content-type"]);
    if (!form || !form.file || !form.metadata) return err(res, 422, "INVALID_METADATA", "file and metadata parts are required");
    let meta;
    try { meta = JSON.parse(form.metadata); } catch { return err(res, 422, "INVALID_METADATA", "metadata is not JSON"); }
    if (!ID_RE.test(meta.dataset_id || "") || !(meta.version >= 1)) return err(res, 422, "INVALID_METADATA", "dataset_id/version invalid");
    const ext = (form.file.filename.split(".").pop() || "").toLowerCase();
    if (!["tif", "tiff", "geojson", "json"].includes(ext)) return err(res, 415, "UNSUPPORTED_MEDIA_TYPE", "only GeoTIFF and GeoJSON");
    const versions = state.versions.get(meta.dataset_id) || [];
    if (versions.some((v) => v.version === meta.version)) return err(res, 409, "VERSION_EXISTS", `version ${meta.version} already exists`);
    const v = { version: meta.version, status: "staged", meta, file: form.file.content, size_bytes: form.file.content.length,
      checksum_sha256: crypto.createHash("sha256").update(form.file.content).digest("hex"), crs: null, created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(), created_by: s.user, provenance: meta.provenance || {} };
    versions.push(v);
    state.versions.set(meta.dataset_id, versions);
    v.status = "validating";
    const job = startAdminJob("upload", meta.dataset_id, meta.version, () => { const r = validateVersion(v); v.crs = v.raster?.epsg ? `EPSG:${v.raster.epsg}` : "EPSG:4326"; return r; });
    return send(res, 202, job);
  }
  if (parts[1] === "datasets" && parts.length === 3 && req.method === "GET") {
    const versions = state.versions.get(parts[2]);
    if (!versions) return err(res, 404, "DATASET_NOT_FOUND", "unknown dataset");
    return send(res, 200, { ...datasetSummary(parts[2]), versions: versions.map(({ file, raster, fc, meta, ...rest }) => rest) });
  }
  if (parts[1] === "datasets" && parts[3] === "versions" && parts.length === 6 && req.method === "POST") {
    const versions = state.versions.get(parts[2]);
    const v = versions?.find((x) => x.version === Number(parts[4]));
    if (!v) return err(res, 404, "VERSION_NOT_FOUND", "unknown version");
    if (parts[5] === "validate") {
      if (!["staged", "failed"].includes(v.status) || !v.file) return err(res, 409, "INVALID_STATE", `cannot validate a ${v.status} version`);
      v.status = "validating";
      return send(res, 202, startAdminJob("validate", parts[2], v.version, () => validateVersion(v)));
    }
    if (parts[5] === "publish") {
      if (v.status !== "validated") return err(res, 409, "INVALID_STATE", `cannot publish a ${v.status} version`);
      v.status = "publishing";
      return send(res, 202, startAdminJob("publish", parts[2], v.version, () => publishVersion(parts[2], v)));
    }
  }
  if (parts[1] === "jobs" && parts.length === 3 && req.method === "GET") {
    const j = state.adminJobs.get(parts[2]);
    return j ? send(res, 200, j) : err(res, 404, "JOB_NOT_FOUND", "unknown job");
  }
  return err(res, 404, "NOT_FOUND", "not found");
}

// ------------------------------------------------------------- exports

const FORMATS = {
  raster: [{ id: "GTiff", label: "GeoTIFF", media_type: "image/tiff; application=geotiff", extension: "tif" }, { id: "COG", label: "Cloud-optimised GeoTIFF", media_type: "image/tiff; application=geotiff; profile=cloud-optimized", extension: "tif" }],
  vector: [{ id: "GeoJSON", label: "GeoJSON", media_type: "application/geo+json", extension: "geojson" }],
};

async function exportsApi(req, res, url, parts, body) {
  if (parts[2] === "capabilities" && req.method === "GET") {
    return send(res, 200, { kinds: ["full", "clip"], formats: FORMATS, output_crs: { suggested: [{ id: "EPSG:4326", label: "WGS 84" }, { id: "EPSG:3857", label: "Web Mercator" }, { id: "EPSG:7755", label: "WGS 84 / India NSF LCC" }], accepts_any_epsg: true } });
  }
  const s = requireUser(req, res, { mutating: req.method === "POST" });
  if (!s) return;
  if (parts.length === 2 && req.method === "POST") {
    if (!body) return err(res, 422, "INVALID_REQUEST", "JSON body required");
    const d = allDatasets().find((x) => x.id === body.dataset_id);
    if (!d) return err(res, 404, "DATASET_NOT_FOUND", "dataset not found");
    if (!["full", "clip"].includes(body.kind)) return err(res, 422, "INVALID_REQUEST", "kind must be full or clip");
    if (!(FORMATS[d.kind] || []).some((f) => f.id === body.format)) return err(res, 422, "UNSUPPORTED_FORMAT", `format ${body.format} is not offered for ${d.kind} datasets`);
    if (body.kind === "clip" && !body.aoi) return err(res, 422, "INVALID_REQUEST", "aoi required for clip");
    if (body.output_crs && !/^EPSG:\d{4,6}$/.test(body.output_crs)) return err(res, 422, "UNSUPPORTED_CRS", "output_crs must be EPSG:<code>");
    const job = { job_id: crypto.randomUUID(), status: "queued", progress: 0, input: body, result: null, error: null, links: {} };
    job.links = { self: `/api/exports/${job.job_id}`, cancel: `/api/exports/${job.job_id}/cancel` };
    state.exports.set(job.job_id, job);
    const steps = 4;
    let i = 0;
    const tick = () => {
      if (job.status === "cancelled") return;
      i += 1;
      job.status = "running"; job.progress = Math.min(0.95, i / steps);
      if (i < steps) { job.timer = setTimeout(tick, 250 + state.delayMs); return; }
      if (d.kind === "raster") {
        const out = buildRasterExport(d, body.kind === "clip" ? (body.aoi.type === "Feature" ? body.aoi.geometry : body.aoi) : null, body.output_crs);
        if (out.error) { job.status = "failed"; job.error = { code: out.error.code, message: out.error.message }; return; }
        job.file = out.bytes; job.filename = `${body.kind}_${d.id}_v${d.version || 1}.tif`;
        job.result = { filename: job.filename, media_type: FORMATS.raster.find((f) => f.id === body.format).media_type, size_bytes: out.bytes.length,
          output: { crs: out.crs, width: out.width, height: out.height, resampled: out.resampled, warnings: out.resampled ? ["MOCK: grid coarsened or reprojected; totals not preserved"] : [] },
          provenance: { dataset_id: d.id, version: d.version || 1, title: d.title, source: d.source, license: d.license || null, checksum_sha256: state.versions.get(d.id)?.[0]?.checksum_sha256 } };
      } else {
        const aoi = body.kind === "clip" ? (body.aoi.type === "Feature" ? body.aoi.geometry : body.aoi) : null;
        const xs = []; const ys = [];
        if (aoi) { const walk = (c) => { if (typeof c[0] === "number") { xs.push(c[0]); ys.push(c[1]); } else c.forEach(walk); }; walk(aoi.coordinates); }
        const box = aoi ? [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] : null;
        const feats = d.features.filter((f) => !box || intersects(f.bbox, box)).map(({ bbox, ...f }) => f);
        job.file = Buffer.from(JSON.stringify({ type: "FeatureCollection", features: feats }));
        job.filename = `${body.kind}_${d.id}_v${d.version || 1}.geojson`;
        job.result = { filename: job.filename, media_type: "application/geo+json", size_bytes: job.file.length, output: { crs: "EPSG:4326", features: feats.length },
          provenance: { dataset_id: d.id, version: d.version || 1, title: d.title, source: d.source } };
      }
      job.status = "completed"; job.progress = 1;
      job.result.download_url = `/api/exports/${job.job_id}/download`;
      job.links.download = job.result.download_url;
      delete job.links.cancel;
    };
    job.timer = setTimeout(tick, 200 + state.delayMs);
    return send(res, 202, strip(job));
  }
  const job = state.exports.get(parts[2]);
  if (!job) return err(res, 404, "JOB_NOT_FOUND", "unknown export job");
  if (parts.length === 3 && req.method === "GET") return send(res, 200, strip(job));
  if (parts[3] === "cancel" && req.method === "POST") {
    if (["completed", "failed", "cancelled"].includes(job.status)) return err(res, 409, "JOB_FINISHED", "job already finished");
    clearTimeout(job.timer); job.status = "cancelled"; delete job.links.cancel;
    return send(res, 200, strip(job));
  }
  if (parts[3] === "download" && req.method === "GET") {
    if (job.status !== "completed") return err(res, 409, "JOB_NOT_COMPLETED", "not ready");
    return send(res, 200, job.file, { "Content-Type": job.result.media_type, "Content-Disposition": `attachment; filename="${job.filename}"` });
  }
  return err(res, 404, "NOT_FOUND", "not found");
}
const strip = ({ file, timer, filename, ...rest }) => rest;

// ------------------------------------------------------------- static viewer

const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".md": "text/markdown" };
function serveStatic(req, res, rel) {
  if (rel === "" || rel.endsWith("/")) rel += "index.html";
  if (rel === "config.json") {
    const cfg = JSON.parse(fs.readFileSync(path.join(CLIENT_ROOT, "config.json"), "utf8"));
    cfg.applicationApi = { ...cfg.applicationApi, enabled: APP_API };
    return send(res, 200, cfg);
  }
  const file = path.resolve(CLIENT_ROOT, rel);
  if (!file.startsWith(CLIENT_ROOT + path.sep) || /[\\/](tests|docs|node_modules)[\\/]/.test(file.slice(CLIENT_ROOT.length))) return err(res, 404, "NOT_FOUND", "not found");
  fs.readFile(file, (e, data) => {
    if (e) return err(res, 404, "NOT_FOUND", "not found");
    res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-store" });
    res.end(data);
  });
}

// ------------------------------------------------------------- router

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (!parts[0]?.startsWith("__mock")) state.requests.push({ method: req.method, path: url.pathname, query: url.search, t: Date.now() });
  try {
    if (url.pathname === "/") { res.writeHead(302, { Location: "/viewer/" }); return res.end(); }
    if (parts[0] === "__mock") {
      if (parts[1] === "reset") { reset(); return send(res, 200, { ok: true }); }
      if (parts[1] === "requests") return send(res, 200, state.requests);
      if (parts[1] === "clear-requests") { state.requests = []; return send(res, 200, { ok: true }); }
      if (parts[1] === "expire-sessions") { for (const s of state.sessions.values()) s.expires = Date.now() - 1; return send(res, 200, { ok: true }); }
      if (parts[1] === "delay") { state.delayMs = Number(url.searchParams.get("ms") || 0); return send(res, 200, { ok: true }); }
      if (parts[1] === "geotiff") {
        // Build an uploadable fixture: 40x30 float32 grid with a gradient, EPSG:4326, NoData -1.
        const w = 40; const h = 30; const v = new Float32Array(w * h);
        for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) v[r * w + c] = c < 2 ? -1 : (c + r) * 2;
        return send(res, 200, encodeGeoTiff(w, h, v, { originX: 77.1, originY: 28.8, resX: 0.01, resY: 0.01, epsg: 4326, nodata: -1 }), { "Content-Type": "image/tiff" });
      }
      return err(res, 404, "NOT_FOUND", "unknown hook");
    }
    if (parts[0] === "viewer") {
      if (url.pathname === "/viewer") { res.writeHead(301, { Location: "/viewer/" }); return res.end(); }
      return serveStatic(req, res, url.pathname.slice("/viewer/".length));
    }
    if (parts[0] === "ogc") return ogc(req, res, url, parts);
    if (parts[0] === "tiles") return vendor(req, res, url, parts);
    const json = req.method === "POST" && /json/.test(req.headers["content-type"] || "") ? await readJson(req) : null;
    if (APP_API && parts[0] === "auth") return auth(req, res, parts, json);
    if (APP_API && parts[0] === "admin") return admin(req, res, url, parts);
    if (APP_API && parts[0] === "api" && parts[1] === "exports") return exportsApi(req, res, url, parts, json);
    if (parts[0] === "api") {
      const handled = processing(req, res, url, parts, json);
      if (handled !== null) return handled;
    }
    return err(res, 404, "NOT_FOUND", "no route; see /viewer/, /ogc/, /tiles/, /api/");
  } catch (e) {
    console.error(e);
    return err(res, 500, "INTERNAL_ERROR", "mock gateway error");
  }
});

server.keepAliveTimeout = 65_000;
server.headersTimeout = 66_000;
server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock gateway on http://127.0.0.1:${PORT}/viewer/ (datasets ${DATASET_SET}, app-api ${APP_API ? "on" : "off"}, LIKE ${LIKE ? "on" : "off"}, shared-style ${SHARED_STYLE})`);
});
