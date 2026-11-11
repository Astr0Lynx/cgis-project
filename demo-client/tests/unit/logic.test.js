import { test } from "node:test";
import assert from "node:assert/strict";
import { loadInBbox, clampBbox, itemsUrl, ViewportLoader } from "../../js/boundaries.js";
import { parseStyle, sharedRange, niceCeil, scaleCompatible, rampCss } from "../../js/legend.js";
import { buildExportRequest, FALLBACK_FORMATS } from "../../js/exports.js";
import { validateUpload } from "../../js/admin.js";
import { parseHash, formatHash } from "../../js/state.js";
import { explain, sameOriginPath } from "../../js/http.js";

const ORIGIN = "http://gw.test";

// A fake OGC items endpoint with N features, pygeoapi-style absolute next links.
function fakeServer(n, { pageCap = 1000 } = {}) {
  const calls = [];
  const fetchJson = async (url, { signal } = {}) => {
    if (signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    calls.push(url);
    const u = new URL(url, ORIGIN);
    const limit = Math.min(pageCap, Number(u.searchParams.get("limit")));
    const offset = Number(u.searchParams.get("offset") || 0);
    const features = Array.from({ length: Math.max(0, Math.min(limit, n - offset)) }, (_, i) => ({ id: `f${offset + i}`, properties: {} }));
    const links = offset + limit < n ? [{ rel: "next", href: `${ORIGIN}${u.pathname}?${new URLSearchParams({ ...Object.fromEntries(u.searchParams), offset: String(offset + limit) })}` }] : [];
    return { type: "FeatureCollection", features, numberMatched: n, links };
  };
  return { fetchJson, calls };
}

test("boundaries: follows rel=next until every matched feature is loaded (no 1000 truncation)", async () => {
  const s = fakeServer(2350);
  const r = await loadInBbox({ base: "/ogc", collectionId: "d", bbox: [70, 10, 80, 20], fetchJson: s.fetchJson, origin: ORIGIN, options: { pageSize: 500, maxFeaturesPerView: 5000 } });
  assert.equal(r.status, "ok");
  assert.equal(r.features.length, 2350);
  assert.equal(r.pages, 5);
  assert.ok(s.calls.every((u) => u.includes("bbox=")), "every request is bbox-scoped");
  assert.ok(s.calls.every((u) => !u.includes("limit=1000")));
});

test("boundaries: more matches than the budget => nothing partial is drawn, one request only", async () => {
  const s = fakeServer(12000);
  const r = await loadInBbox({ base: "/ogc", collectionId: "d", bbox: [68, 6, 98, 36], fetchJson: s.fetchJson, origin: ORIGIN, options: { pageSize: 500, maxFeaturesPerView: 5000 } });
  assert.equal(r.status, "too_many");
  assert.equal(r.features.length, 0);
  assert.equal(r.numberMatched, 12000);
  assert.equal(s.calls.length, 1);
});

test("boundaries: server page caps are respected by following next links", async () => {
  const s = fakeServer(1300, { pageCap: 100 });
  const r = await loadInBbox({ base: "/ogc", collectionId: "d", bbox: [70, 10, 80, 20], fetchJson: s.fetchJson, origin: ORIGIN, options: { pageSize: 500, maxFeaturesPerView: 5000 } });
  assert.equal(r.features.length, 1300);
  assert.equal(r.pages, 13);
});

test("boundaries: cross-origin next links are never followed", async () => {
  const fetchJson = async () => ({ features: [{ id: 1 }], numberMatched: 50, links: [{ rel: "next", href: "http://evil.example/x" }] });
  const r = await loadInBbox({ base: "/ogc", collectionId: "d", bbox: [0, 0, 1, 1], fetchJson, origin: ORIGIN });
  assert.equal(r.features.length, 1);
  assert.equal(r.pages, 1);
});

test("boundaries: a newer view aborts the older load and only the newest state is reported", async () => {
  const states = [];
  let release;
  const gate = new Promise((r) => { release = r; });
  let first = true;
  const fetchJson = async (url, { signal }) => {
    if (first) {
      first = false;
      await gate;
      if (signal.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    }
    return { features: [{ id: url }], numberMatched: 1, links: [] };
  };
  const loader = new ViewportLoader({ base: "/ogc", collectionId: "d", extent: null, fetchJson, origin: ORIGIN, onState: (s) => states.push(s.status) });
  const p1 = loader.load([0, 0, 1, 1]);
  const p2 = loader.load([2, 2, 3, 3]);
  release();
  assert.equal(await p1, null);
  const r2 = await p2;
  assert.equal(r2.status, "ok");
  assert.deepEqual(states.filter((s) => s !== "loading"), ["ok"]);
});

test("boundaries: bbox clamped to the collection extent; no request when outside", () => {
  assert.deepEqual(clampBbox([60, 0, 100, 40], [68, 6, 98, 36]), [68, 6, 98, 36]);
  assert.equal(clampBbox([0, 0, 10, 10], [68, 6, 98, 36]), null);
  assert.ok(itemsUrl("/ogc", "a b", [1, 2, 3, 4], 500).startsWith("/ogc/collections/a%20b/items?f=json&bbox=1%2C2%2C3%2C4&limit=500"));
});

test("legend: OGC style parsing and shared-range detection", () => {
  assert.deepEqual(parseStyle({ title: "viridis linear 0..2", description: "Range: dataset range (min(0, minimum) to maximum); not comparable across datasets." }),
    { colormap: "viridis", lo: 0, hi: 2, shared: false, source: "ogc-style" });
  assert.equal(parseStyle({ title: "viridis linear 0..1000", description: "Range: configured shared range." }).shared, true);
  assert.equal(parseStyle({ title: "something else" }), null);
  assert.equal(parseStyle(null), null);
});

test("legend: shared range = [min(0,min), nice(max p98)] across datasets", () => {
  const r = sharedRange([{ b1: { min: 0, max: 900, percentile_98: 180 } }, { b1: { min: 0, max: 1200, percentile_98: 231 } }]);
  assert.deepEqual([r.lo, r.hi, r.clipped], [0, 250, true]);
  assert.equal(niceCeil(231), 250);
  assert.equal(niceCeil(1.2), 2);
  assert.equal(niceCeil(0.003), 0.005);
  assert.equal(sharedRange([]), null);
  assert.ok(scaleCompatible([{ units: "people_per_cell" }, { units: "people_per_cell" }]));
  assert.ok(!scaleCompatible([{ units: "people_per_cell" }, { units: "people_per_km2" }]));
  assert.ok(rampCss().startsWith("linear-gradient(90deg, #440154 0.0%"));
});

test("exports: validation of dataset/kind/CRS/format, payload shape", () => {
  const ds = { id: "india-l1-pop-2011", kind: "raster" };
  const aoi = { geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } };
  const ok = buildExportRequest({ dataset: ds, kind: "clip", aoi, crsChoice: "EPSG:3857", format: "COG", formats: FALLBACK_FORMATS });
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.payload, { dataset_id: ds.id, kind: "clip", aoi: aoi.geometry, aoi_crs: "EPSG:4326", output_crs: "EPSG:3857", format: "COG" });
  const native = buildExportRequest({ dataset: ds, kind: "full", crsChoice: "native", format: "GTiff", formats: FALLBACK_FORMATS, version: "2" });
  assert.deepEqual(native.payload, { dataset_id: ds.id, version: 2, kind: "full", format: "GTiff" });
  assert.equal(buildExportRequest({ dataset: ds, kind: "clip", aoi: null, crsChoice: "native", format: "GTiff", formats: FALLBACK_FORMATS }).errors.kind !== undefined, true);
  assert.ok(buildExportRequest({ dataset: ds, kind: "full", crsChoice: "custom", customCrs: "/etc/passwd", format: "GTiff", formats: FALLBACK_FORMATS }).errors.crs);
  assert.equal(buildExportRequest({ dataset: ds, kind: "full", crsChoice: "custom", customCrs: "epsg:32644", format: "GTiff", formats: FALLBACK_FORMATS }).payload.output_crs, "EPSG:32644");
  assert.ok(buildExportRequest({ dataset: ds, kind: "full", crsChoice: "native", format: "GeoJSON", formats: FALLBACK_FORMATS }).errors.format);
});

test("admin: upload validation mirrors the importer contract", () => {
  const file = { name: "x.tif", size: 10 };
  const good = { dataset_id: "india-l2-pop-2011-pune", version: 1, title: "t", kind: "raster", product_level: "level2", year: 2011, units: "people_per_cell" };
  assert.equal(validateUpload(good, file).ok, true);
  assert.ok(validateUpload({ ...good, dataset_id: "../etc" }, file).errors.dataset_id);
  assert.ok(validateUpload({ ...good, version: 0 }, file).errors.version);
  assert.ok(validateUpload({ ...good, units: undefined }, file).errors.units);
  assert.ok(validateUpload({ ...good, kind: "vector" }, file).errors.kind);
  assert.ok(validateUpload(good, { name: "x.shp", size: 1 }).errors.file);
  assert.ok(validateUpload(good, null).errors.file);
  assert.equal(validateUpload({ ...good, kind: "vector", units: undefined }, { name: "b.geojson" }).ok, true);
});

test("hash state round-trips", () => {
  const h = formatHash("map", { a: "india-l1-pop-2005", b: "india-l1-pop-2011", mode: "swipe", src: "vendor", scale: "shared", c: "78.5,22.1,5.00" });
  const p = parseHash(h);
  assert.deepEqual([p.view, p.a, p.b, p.mode, p.src, p.scale], ["map", "india-l1-pop-2005", "india-l1-pop-2011", "swipe", "vendor", "shared"]);
  assert.deepEqual(p.camera, { center: [78.5, 22.1], zoom: 5 });
  assert.equal(parseHash("#/admin").view, "admin");
  assert.equal(parseHash("#/whatever").view, "map");
});

test("http helpers: error explanations and same-origin paths", () => {
  assert.match(explain({ code: "AOI_TOO_LARGE" }), /too large/);
  assert.match(explain({ status: 403, code: "HTTP_403" }), /permission/);
  assert.equal(sameOriginPath("http://gw.test/ogc/x?a=1", "http://gw.test"), "/ogc/x?a=1");
  assert.equal(sameOriginPath("http://other/x", "http://gw.test"), null);
});
