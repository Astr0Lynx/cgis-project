// CyberGIS viewer (Agent 6): the Phase 2 diagnostic MapLibre viewer, extended into the application
// layer of the original flowchart — layer-wise visualisation, comparisons, area analysis, downloads
// and role-based dataset administration.
//
// Rules this file keeps:
//  * Everything is discovered from the OGC API (plus vendor/optional metadata); no dataset ids are
//    hard-coded and nothing assumes a particular city.
//  * Statistics come from the processing service; the browser never computes population values.
//  * OGC API - Tiles and the vendor-specific TiTiler routes are both available and always labelled.
"use strict";

import { $, toast } from "./js/dom.js";
import { getJson, request } from "./js/http.js";
import { createStore, parseHash, formatHash } from "./js/state.js";
import { buildCatalog } from "./js/catalog.js";
import { createAuth } from "./js/auth.js";
import { createExports } from "./js/exports.js";
import { createAdmin } from "./js/admin.js";
import { createVectors } from "./js/vectors.js";
import { createPanels } from "./js/panels.js";
import { createDisplay } from "./js/display.js";
import { createCompare } from "./js/compare.js";
import { createInspector } from "./js/inspector.js";
import { createAnalysis } from "./js/analysis.js";
import { createSearch } from "./js/search.js";

const OGC = "/ogc";
const API = "/api";
const TILES = "/tiles";
const ORIGIN = window.location.origin;

// Custom processing API (not OGC API - Processes). Phase 2 routes, unchanged.
const OPERATIONS = {
  zonal: `${API}/operations/zonal-statistics`,
  clip: `${API}/operations/clip`,
  job: (id) => `${API}/operations/${encodeURIComponent(id)}`,
  download: (id) => `${API}/downloads/${encodeURIComponent(id)}`,
};

const DEFAULT_CONFIG = {
  applicationApi: { enabled: false, auth: "/auth", admin: "/admin", exports: "/api/exports" },
  catalogMetadata: null,
  studyArea: { name: "India", bbox: [68.1, 6.5, 97.5, 35.7] },
  boundaries: { pageSize: 500, maxFeaturesPerView: 5000 },
};

// ---------------------------------------------------------------- tile routes (both labelled)

/**
 * Raster tile template for one dataset.
 *  - "ogc":    OGC API - Tiles, order {tileMatrix}/{tileRow}/{tileCol} = {z}/{y}/{x}.
 *  - "vendor": TiTiler, order {z}/{x}/{y}; vendor-specific, not an OGC standard. Takes an explicit
 *              rescale so colours match the legend (the published OGC range or a shared range).
 */
export function rasterTileUrl(id, source, render = {}) {
  const enc = encodeURIComponent(id);
  if (source === "vendor") {
    const q = new URLSearchParams({ colormap_name: render.colormap || "viridis" });
    if (render.rescale) q.set("rescale", render.rescale.map((v) => +v.toPrecision(6)).join(","));
    return `${ORIGIN}${TILES}/datasets/${enc}/tiles/WebMercatorQuad/{z}/{x}/{y}.png?${q}`;
  }
  return `${ORIGIN}${OGC}/collections/${enc}/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png`;
}

const tilesetCache = new Map();
const tilejsonCache = new Map();

/** OGC tileset metadata (tileMatrixSetLimits, boundingBox, style); cached per dataset. */
export function tilesetMeta(id) {
  if (!tilesetCache.has(id)) {
    const p = getJson(`${OGC}/collections/${encodeURIComponent(id)}/tiles/WebMercatorQuad?f=json`)
      .catch((err) => { tilesetCache.delete(id); throw err; });
    tilesetCache.set(id, p);
  }
  return tilesetCache.get(id);
}

// Zoom range and bounds as advertised by the server, so MapLibre over-zooms the deepest level
// instead of requesting tiles outside the tileset limits (the server answers those with 404).
export async function rasterTileRange(id, source) {
  const enc = encodeURIComponent(id);
  try {
    if (source === "vendor") {
      if (!tilejsonCache.has(id)) tilejsonCache.set(id, getJson(`${TILES}/datasets/${enc}/WebMercatorQuad/tilejson.json`));
      const tj = await tilejsonCache.get(id);
      return { minzoom: tj.minzoom ?? 0, maxzoom: tj.maxzoom ?? 22, bounds: tj.bounds };
    }
    const meta = await tilesetMeta(id);
    const zooms = (meta.tileMatrixSetLimits || []).map((l) => Number(l.tileMatrix));
    const bb = meta.boundingBox;
    return {
      minzoom: zooms.length ? Math.min(...zooms) : 0,
      maxzoom: zooms.length ? Math.max(...zooms) : 22,
      bounds: bb ? [...bb.lowerLeft, ...bb.upperRight] : undefined,
    };
  } catch (err) {
    tilejsonCache.delete(id);
    return {};
  }
}

// ---------------------------------------------------------------- diagnostics log

const out = (value) => {
  $("output").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
};

// ---------------------------------------------------------------- boot

async function loadConfig() {
  try {
    const res = await request("config.json");
    if (res.ok && res.body && typeof res.body === "object") {
      return {
        ...DEFAULT_CONFIG, ...res.body,
        applicationApi: { ...DEFAULT_CONFIG.applicationApi, ...(res.body.applicationApi || {}) },
        boundaries: { ...DEFAULT_CONFIG.boundaries, ...(res.body.boundaries || {}) },
        studyArea: { ...DEFAULT_CONFIG.studyArea, ...(res.body.studyArea || {}) },
      };
    }
  } catch { /* defaults */ }
  return DEFAULT_CONFIG;
}

const config = await loadConfig();
const initialHash = parseHash();
const store = createStore({
  view: initialHash.view,
  tab: "layers",
  a: initialHash.a || null,
  b: initialHash.b || null,
  mode: ["single", "swipe", "side"].includes(initialHash.mode) ? initialHash.mode : "single",
  src: initialHash.src === "vendor" ? "vendor" : "ogc",
  scale: initialHash.scale === "shared" ? "shared" : "layer",
  opacity: 0.8,
  visible: true,
  overlayL2: false,
  aoi: null,
  session: { status: "unknown", user: null },
  catalogStatus: "loading",
});

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": "#dde3e1" } }],
  },
  bounds: config.studyArea.bbox,
  fitBoundsOptions: { padding: 24 },
  attributionControl: { compact: true, customAttribution: "Data: CyberGIS catalog via OGC API · see each layer's provenance" },
  dragRotate: false,
  pitchWithRotate: false,
});
map.touchZoomRotate.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
window.map = map;   // used by browser tests and handy in the console

const ctx = {
  config, store, map, out, OGC, API, TILES, ORIGIN, OPERATIONS,
  catalog: null,
  tileUrl: rasterTileUrl,
  tileRange: rasterTileRange,
  tilesetMeta,
  getDataset: (id) => (ctx.catalog && id ? ctx.catalog.byId.get(id) : null),
};

const auth = createAuth({ config, store, onChange: () => { exportsUi.render(); panels.renderAccess(); } });
ctx.auth = auth;
const exportsUi = createExports({ config, store, auth, getCatalog: () => ctx.catalog, out });
const admin = createAdmin({ config, store, auth, reloadCatalog: () => discover({ keepView: true }) });
const vectors = createVectors(ctx);
ctx.vectors = vectors;
const display = createDisplay(ctx);
ctx.display = display;
const compare = createCompare(ctx);
ctx.compare = compare;
const panels = createPanels(ctx);
ctx.panels = panels;
const inspector = createInspector(ctx);
ctx.inspector = inspector;
const analysis = createAnalysis(ctx);
ctx.analysis = analysis;
createSearch(ctx);

// ---------------------------------------------------------------- tabs

const TABS = ["layers", "analysis", "download"];
function setTab(name, { focus = false } = {}) {
  for (const t of TABS) {
    const btn = $(`tab-btn-${t}`);
    const on = t === name;
    btn.setAttribute("aria-selected", on ? "true" : "false");
    btn.tabIndex = on ? 0 : -1;
    $(`tab-${t}`).hidden = !on;
    if (on && focus) btn.focus();
  }
  store.set({ tab: name });
}
ctx.setTab = setTab;
for (const t of TABS) $(`tab-btn-${t}`).addEventListener("click", () => setTab(t));
document.querySelector(".tabs").addEventListener("keydown", (e) => {
  const i = TABS.indexOf(store.get().tab);
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    setTab(TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length], { focus: true });
  }
});

// ---------------------------------------------------------------- views / routing

function applyRoute() {
  const route = parseHash();
  store.set({ view: route.view });
  $("view-map").hidden = route.view !== "map";
  $("view-admin").hidden = route.view !== "admin";
  $("nav-map").setAttribute("aria-current", route.view === "map" ? "page" : "false");
  $("nav-admin").setAttribute("aria-current", route.view === "admin" ? "page" : "false");
  if (route.view === "admin") admin.open();
  else requestAnimationFrame(() => { map.resize(); compare.resize(); });
}
window.addEventListener("hashchange", applyRoute);

let hashTimer = 0;
function writeHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const s = store.get();
    if (s.view !== "map") return;
    const c = map.getCenter();
    const next = formatHash("map", {
      a: s.a, b: s.mode !== "single" ? s.b : null, mode: s.mode !== "single" ? s.mode : null,
      src: s.src !== "ogc" ? s.src : null, scale: s.scale !== "layer" ? s.scale : null,
      c: `${c.lng.toFixed(4)},${c.lat.toFixed(4)},${map.getZoom().toFixed(2)}`,
    });
    if (next !== window.location.hash) history.replaceState(null, "", next);
  }, 250);
}
map.on("moveend", writeHash);

// ---------------------------------------------------------------- map activity indicator

let busyTimer = 0;
map.on("dataloading", () => { clearTimeout(busyTimer); $("map-progress").hidden = false; });
map.on("idle", () => { busyTimer = setTimeout(() => { $("map-progress").hidden = true; }, 120); });
map.on("error", (e) => {
  // Tile failures are reported once per source in the legend rather than spamming the console.
  const sourceId = e && e.sourceId;
  if (sourceId) display.reportTileError(sourceId, e.error);
});

// ---------------------------------------------------------------- discovery

async function discover({ keepView = false } = {}) {
  store.set({ catalogStatus: "loading" });
  panels.renderLoading();
  let collections;
  try {
    ({ collections = [] } = await getJson(`${OGC}/collections?f=json`));
  } catch (err) {
    store.set({ catalogStatus: "error" });
    panels.renderDiscoveryError(err, () => discover({ keepView }));
    out({ error: err.message, code: err.code, status: err.status });
    return;
  }
  const [vendor, metadata] = await Promise.all([
    getJson(`${TILES}/datasets`).then((b) => b.datasets || []).catch(() => []),
    config.catalogMetadata
      ? getJson(config.catalogMetadata).then((b) => b.items || []).catch(() => [])
      : Promise.resolve([]),
  ]);
  const catalog = buildCatalog({ collections, vendor, metadata, studyBbox: config.studyArea.bbox });
  ctx.catalog = catalog;
  tilesetCache.clear();
  tilejsonCache.clear();

  // Pick layer A: hash, else the latest year of the first national Level 1 series, else first raster.
  const s = store.get();
  let a = s.a && catalog.byId.get(s.a)?.kind === "raster" ? s.a : null;
  if (!a) {
    const firstSeries = catalog.series[0];
    a = firstSeries ? firstSeries.byYear.get(firstSeries.years[0]) : catalog.rasters[0]?.id || null;
  }
  let b = s.b && catalog.byId.get(s.b)?.kind === "raster" ? s.b : null;
  if (!b && a) b = display.defaultB(a);
  store.set({ a, b, catalogStatus: catalog.datasets.length ? "ready" : "empty" });

  panels.renderAll();
  await vectors.init(catalog.vectors);
  await display.refresh();
  exportsUi.render();
  analysis.refreshButtons();

  if (!keepView && !initialHash.camera) {
    // India-wide by default. Only when nothing national is published (e.g. the Phase 2 fixtures)
    // does the camera follow the published data instead of showing an empty country.
    const target = catalog.nationalExtent ? null : catalog.dataExtent;
    if (target) map.fitBounds([[target[0], target[1]], [target[2], target[3]]], { padding: 30, duration: 0 });
  }

  out({
    collections: collections.map((c) => ({ id: c.id, title: c.title, kind: catalog.byId.get(c.id)?.kind === "raster" ? "raster (tiles)" : "vector (items)" })),
    note: "Click a boundary to select it as the AOI, then run an operation.",
  });
}
ctx.discover = discover;

// ---------------------------------------------------------------- controls

$("raster").addEventListener("change", (e) => store.set({ a: e.target.value || null }));
$("raster-b").addEventListener("change", (e) => store.set({ b: e.target.value || null }));
document.querySelectorAll("input[name=source]").forEach((r) => r.addEventListener("change", (e) => {
  const src = e.target.value;
  // OGC tiles cannot take a client range; leaving TiTiler drops a client-side shared range unless
  // the published OGC styles already agree.
  const patch = { src };
  if (src === "ogc" && store.get().scale === "shared" && !display.ogcStylesShared()) patch.scale = "layer";
  store.set(patch);
}));
document.querySelectorAll("input[name=scale]").forEach((r) => r.addEventListener("change", (e) => {
  const scale = e.target.value;
  const patch = { scale };
  if (scale === "shared" && !display.ogcStylesShared()) patch.src = "vendor";
  store.set(patch);
}));
document.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", (e) => store.set({ mode: e.target.value })));
$("raster-visible").addEventListener("change", (e) => store.set({ visible: e.target.checked }));
$("opacity").addEventListener("input", (e) => store.set({ opacity: Number(e.target.value) }));

// Reflect store -> controls and re-render dependent parts.
store.subscribe((s, changed) => {
  const has = (...keys) => keys.some((k) => changed.includes(k));
  if (has("src")) document.querySelectorAll("input[name=source]").forEach((r) => { r.checked = r.value === s.src; });
  if (has("scale")) document.querySelectorAll("input[name=scale]").forEach((r) => { r.checked = r.value === s.scale; });
  if (has("mode")) document.querySelectorAll("input[name=mode]").forEach((r) => { r.checked = r.value === s.mode; });
  if (has("opacity")) $("opacity-value").textContent = `${Math.round(s.opacity * 100)}%`;
  if (has("a") && $("raster").value !== (s.a || "")) $("raster").value = s.a || "";
  if (has("b") && $("raster-b").value !== (s.b || "")) $("raster-b").value = s.b || "";
  if (!ctx.catalog) return;
  if (has("a", "b", "mode", "src", "scale", "overlayL2")) {
    display.refresh();
    panels.renderCatalog();
    panels.renderActive();
    analysis.refreshButtons();
  }
  if (has("mode")) compare.setMode(s.mode);
  if (has("opacity", "visible")) display.applyPaint();
  if (has("a", "b", "mode", "src", "scale", "opacity")) writeHash();
});

// ---------------------------------------------------------------- start

// Controls start from the store (which may come from a shared link), not from the HTML defaults.
(function syncControls() {
  const s = store.get();
  document.querySelectorAll("input[name=source]").forEach((r) => { r.checked = r.value === s.src; });
  document.querySelectorAll("input[name=scale]").forEach((r) => { r.checked = r.value === s.scale; });
  document.querySelectorAll("input[name=mode]").forEach((r) => { r.checked = r.value === s.mode; });
  $("opacity").value = String(s.opacity);
  $("opacity-value").textContent = `${Math.round(s.opacity * 100)}%`;
  $("raster-visible").checked = s.visible;
})();
applyRoute();
auth.refresh();
exportsUi.loadCapabilities().then(() => exportsUi.render());
if (initialHash.camera) map.jumpTo(initialHash.camera);
map.on("load", () => {
  compare.setMode(store.get().mode);
  discover().catch((err) => {
    out({ error: err.message, body: err.body });
    toast(`Could not start the viewer: ${err.message}`, { kind: "error" });
  });
});
map.on("click", (e) => {
  if (analysis.isDrawing() || e.drawConsumed) return;
  inspector.query(e.lngLat);
  analysis.clickSelect(e.point);
});
