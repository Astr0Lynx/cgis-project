// CyberGIS Phase 2 diagnostic viewer (Agent 6). A test harness, not the Phase 4 application.
// Everything is discovered from the OGC API; no dataset ids are hard-coded.
"use strict";

const OGC = "/ogc";
const API = "/api";
const TILES = "/tiles";
const ORIGIN = window.location.origin;

const state = {
  rasters: [],          // OGC collections that publish tilesets
  vectors: [],          // OGC collections that publish items
  features: new Map(),  // "<collection>/<feature id>" -> GeoJSON feature (untiled geometry)
  aoi: null,            // {label, geometry}
};

const $ = (id) => document.getElementById(id);
const out = (value) => {
  $("output").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
};

async function getJson(url) {
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw Object.assign(new Error(`${resp.status} ${url}`), { body, status: resp.status });
  return body;
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await resp.json().catch(() => ({}));
  return { status: resp.status, ok: resp.ok, body };
}

const hasLink = (collection, pattern) =>
  (collection.links || []).some((l) => pattern.test(l.rel || "") || pattern.test(l.href || ""));

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": "#eef1f4" } }],
  },
  center: [78.032, 17.968],
  zoom: 13,
  attributionControl: { customAttribution: "SYNTHETIC deterministic fixtures" },
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl(), "bottom-left");

function rasterTileUrl(id, source) {
  const enc = encodeURIComponent(id);
  if (source === "vendor") {
    // TiTiler order is {z}/{x}/{y}; fixed rescale so 2001 (1) and 2011 (2) look different.
    return `${ORIGIN}${TILES}/datasets/${enc}/tiles/WebMercatorQuad/{z}/{x}/{y}.png?rescale=0,2&colormap_name=viridis`;
  }
  // OGC API - Tiles order is {tileMatrix}/{tileRow}/{tileCol} = {z}/{y}/{x}.
  return `${ORIGIN}${OGC}/collections/${enc}/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png`;
}

// Zoom range and bounds as advertised by the server, so MapLibre over-zooms the deepest level
// instead of requesting tiles outside the tileset limits (the server answers those with 404).
async function rasterTileRange(id, source) {
  const enc = encodeURIComponent(id);
  try {
    if (source === "vendor") {
      const tj = await getJson(`${TILES}/datasets/${enc}/WebMercatorQuad/tilejson.json`);
      return { minzoom: tj.minzoom ?? 0, maxzoom: tj.maxzoom ?? 22, bounds: tj.bounds };
    }
    const meta = await getJson(`${OGC}/collections/${enc}/tiles/WebMercatorQuad?f=json`);
    const zooms = (meta.tileMatrixSetLimits || []).map((l) => Number(l.tileMatrix));
    const bb = meta.boundingBox;
    return {
      minzoom: zooms.length ? Math.min(...zooms) : 0,
      maxzoom: zooms.length ? Math.max(...zooms) : 22,
      bounds: bb ? [...bb.lowerLeft, ...bb.upperRight] : undefined,
    };
  } catch (err) {
    return {};
  }
}

let rasterRequest = 0;

async function showRaster() {
  const request = ++rasterRequest;
  const id = $("raster").value;
  const source = document.querySelector("input[name=source]:checked").value;
  const range = id ? await rasterTileRange(id, source) : {};
  if (request !== rasterRequest) return;           // a newer selection won
  if (map.getLayer("raster")) map.removeLayer("raster");
  if (map.getSource("raster")) map.removeSource("raster");
  if (!id) return;
  const spec = { type: "raster", tiles: [rasterTileUrl(id, source)], tileSize: 256 };
  for (const key of ["minzoom", "maxzoom", "bounds"]) if (range[key] !== undefined) spec[key] = range[key];
  map.addSource("raster", spec);
  const before = map.getLayer(firstVectorLayer()) ? firstVectorLayer() : undefined;
  map.addLayer({
    id: "raster",
    type: "raster",
    source: "raster",
    layout: { visibility: $("raster-visible").checked ? "visible" : "none" },
    paint: { "raster-opacity": Number($("opacity").value), "raster-resampling": "nearest" },
  }, before);
  updateButtons();
}

const firstVectorLayer = () => (state.vectors[0] ? `v-${state.vectors[0].id}-fill` : "");

async function addVector(collection) {
  const url = `${OGC}/collections/${encodeURIComponent(collection.id)}/items?f=json&limit=1000`;
  const fc = await getJson(url);
  for (const f of fc.features || []) {
    f.properties = { ...(f.properties || {}), _key: `${collection.id}/${f.id}` };
    state.features.set(f.properties._key, f);
  }
  const src = `v-${collection.id}`;
  map.addSource(src, { type: "geojson", data: fc });
  map.addLayer({ id: `${src}-fill`, type: "fill", source: src,
                 paint: { "fill-color": "#ff7f0e", "fill-opacity": 0.12 } });
  map.addLayer({ id: `${src}-line`, type: "line", source: src,
                 paint: { "line-color": "#d14900", "line-width": 2 } });
  map.addLayer({ id: `${src}-selected`, type: "line", source: src, filter: ["==", ["get", "_key"], ""],
                 paint: { "line-color": "#1f6feb", "line-width": 4 } });

  const row = document.createElement("label");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = true;
  box.addEventListener("change", () => {
    for (const suffix of ["fill", "line", "selected"]) {
      map.setLayoutProperty(`${src}-${suffix}`, "visibility", box.checked ? "visible" : "none");
    }
  });
  row.append(box, ` ${collection.title || collection.id} (${(fc.features || []).length} features)`);
  $("vectors").append(row);
  return fc;
}

function selectAoi(label, geometry, featureKey = "") {
  state.aoi = { label, geometry };
  $("aoi-label").textContent = `AOI: ${label}`;
  for (const v of state.vectors) {
    map.setFilter(`v-${v.id}-selected`, ["==", ["get", "_key"], featureKey]);
  }
  updateButtons();
}

function updateButtons() {
  const ready = Boolean(state.aoi && $("raster").value);
  $("zonal").disabled = !ready;
  $("clip").disabled = !ready;
}

async function runZonal() {
  const payload = {
    dataset_id: $("raster").value,
    aoi: state.aoi.geometry,
    aoi_crs: "EPSG:4326",
    operations: ["sum", "mean", "count"],
  };
  out("Running zonal statistics…");
  const { status, body } = await postJson(`${API}/operations/zonal-statistics`, payload);
  out({ request: { ...payload, aoi: state.aoi.label }, http_status: status, response: body });
}

async function runClip() {
  const payload = { dataset_id: $("raster").value, aoi: state.aoi.geometry, aoi_crs: "EPSG:4326", format: "GTiff" };
  out("Clipping…");
  $("download").textContent = "";
  const { status, body } = await postJson(`${API}/operations/clip`, payload);
  out({ request: { ...payload, aoi: state.aoi.label }, http_status: status, response: body });
  if (status < 300 && body.job_id && body.status === "completed") {
    const a = document.createElement("a");
    a.href = `${API}/downloads/${encodeURIComponent(body.job_id)}`;
    a.textContent = "Download clipped GeoTIFF";
    a.setAttribute("data-testid", "download-link");
    $("download").append(a);
  }
}

async function showPixel(lngLat) {
  const id = $("raster").value;
  if (!id) return;
  const url = `${TILES}/datasets/${encodeURIComponent(id)}/point/${lngLat.lng.toFixed(6)},${lngLat.lat.toFixed(6)}`;
  try {
    const body = await getJson(url);
    $("pixel").textContent = `Pixel value at ${lngLat.lng.toFixed(4)}, ${lngLat.lat.toFixed(4)}: ` +
      `${JSON.stringify(body.values ?? body)} (TiTiler /point, vendor-specific)`;
  } catch (err) {
    $("pixel").textContent = `Pixel value: ${err.status || ""} ${(err.body && err.body.error && err.body.error.code) || err.message}`;
  }
}

async function init() {
  const { collections = [] } = await getJson(`${OGC}/collections?f=json`);
  state.rasters = collections.filter((c) => hasLink(c, /tiles/));
  state.vectors = collections.filter((c) => hasLink(c, /\/items/) && !state.rasters.includes(c));

  for (const c of state.rasters) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.title || c.id;
    $("raster").append(opt);
  }

  const extents = [];
  for (const v of state.vectors) {
    const fc = await addVector(v);
    if (fc.bbox) extents.push(fc.bbox);
  }
  await showRaster();

  for (const c of collections) {
    const b = c.extent && c.extent.spatial && c.extent.spatial.bbox && c.extent.spatial.bbox[0];
    if (b) extents.push(b);
  }
  if (extents.length) {
    const w = Math.min(...extents.map((b) => b[0])), s = Math.min(...extents.map((b) => b[1]));
    const e = Math.max(...extents.map((b) => b[2])), n = Math.max(...extents.map((b) => b[3]));
    map.fitBounds([[w, s], [e, n]], { padding: 30, duration: 0 });
  }

  out({
    collections: collections.map((c) => ({ id: c.id, title: c.title, kind: state.rasters.includes(c) ? "raster (tiles)" : "vector (items)" })),
    note: "Click a boundary to select it as the AOI, then run an operation.",
  });
}

map.on("load", () => {
  init().catch((err) => out({ error: err.message, body: err.body }));
});

map.on("click", (e) => {
  showPixel(e.lngLat);
  const layers = state.vectors.map((v) => `v-${v.id}-fill`).filter((id) => map.getLayer(id));
  const hit = layers.length ? map.queryRenderedFeatures(e.point, { layers })[0] : null;
  if (hit) {
    const feature = state.features.get(hit.properties._key);
    if (feature) selectAoi(`${feature.properties.name || feature.id} (${hit.properties._key})`, feature.geometry, hit.properties._key);
  }
});

$("raster").addEventListener("change", showRaster);
document.querySelectorAll("input[name=source]").forEach((r) => r.addEventListener("change", showRaster));
$("raster-visible").addEventListener("change", (e) => {
  if (map.getLayer("raster")) map.setLayoutProperty("raster", "visibility", e.target.checked ? "visible" : "none");
});
$("opacity").addEventListener("input", (e) => {
  if (map.getLayer("raster")) map.setPaintProperty("raster", "raster-opacity", Number(e.target.value));
});
$("aoi-view").addEventListener("click", () => {
  const b = map.getBounds();
  const ring = [[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()],
                [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]];
  selectAoi("current map view", { type: "Polygon", coordinates: [ring] });
});
$("zonal").addEventListener("click", () => runZonal().catch((err) => out({ error: err.message })));
$("clip").addEventListener("click", () => runClip().catch((err) => out({ error: err.message })));
