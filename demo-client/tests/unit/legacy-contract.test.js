// Static port of tests/e2e/test_60_viewer.py (owned by Agent 7) so this branch proves it still holds
// without a running stack. The original remains the authority at integration time.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (f) => fs.readFileSync(path.join(root, f), "utf8");

test("page pins MapLibre 4.7.1 with SRI and labels SYNTHETIC data", () => {
  const html = read("index.html");
  assert.ok(html.includes("maplibre-gl@4.7.1") && html.includes('integrity="sha384-'));
  assert.ok(html.includes("SYNTHETIC"));
  assert.ok(html.includes('src="app.js"') && html.includes('href="style.css"'));
});

test("app.js uses discovery, both tile routes, server zoom limits and the processing API", () => {
  const js = read("app.js");
  assert.ok(!js.includes("synthetic-") && !js.includes("town-a"));
  assert.ok(js.includes("/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png"));
  assert.ok(js.includes('const TILES = "/tiles"') && js.includes("/datasets/${enc}/tiles/WebMercatorQuad/{z}/{x}/{y}.png"));
  assert.ok(js.includes("vendor"));
  assert.ok(js.includes("tileMatrixSetLimits") && js.includes("tilejson.json"));
  assert.ok(js.includes("/operations/zonal-statistics") && js.includes("/operations/clip"));
});

test("no module hard-codes a dataset id, the Phase 2 fixture names, or a Hyderabad camera", () => {
  const files = ["app.js", ...fs.readdirSync(path.join(root, "js")).map((f) => `js/${f}`)];
  for (const f of files) {
    const src = read(f);
    assert.ok(!/synthetic-worldpop|synthetic-towns|town-a|india-l1-pop-|india-l2-pop-/.test(src), `${f} names a dataset`);
    assert.ok(!/78\.03|17\.96|center:\s*\[\s*78/.test(src), `${f} has a fixture camera`);
  }
});

test("every legacy element id used by check_viewer.py exists", () => {
  const html = read("index.html");
  for (const id of ["raster", "vectors", "aoi-label", "zonal", "clip", "output", "download", "aoi-view", "raster-visible", "opacity"]) {
    assert.ok(html.includes(`id="${id}"`), id);
  }
  assert.ok(html.includes('name="source" value="ogc"') && html.includes('name="source" value="vendor"'));
  assert.ok(read("js/inspector.js").includes('id: "pixel"'));
  assert.ok(read("js/analysis.js").includes('"data-testid": "download-link"'));
  assert.ok(read("app.js").includes("window.map = map"));
});

test("the viewer image excludes tests, docs and node_modules", () => {
  const ignore = read(".dockerignore");
  for (const p of ["tests/", "docs/", "node_modules"]) assert.ok(ignore.includes(p), p);
});
