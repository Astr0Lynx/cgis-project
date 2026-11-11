import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseDescriptionFacts, parseKeywords, parseCollection, buildCatalog, provenanceClass, pixelComparability,
  scopeOf, matchesFilter, approxCellMetres,
} from "../../js/catalog.js";

const STUDY = [68.1, 6.5, 97.5, 35.7];
const tilesLink = [{ rel: "http://www.opengis.net/def/rel/ogc/1.0/tilesets-map", href: "/ogc/collections/x/tiles" }];
const itemsLink = [{ rel: "items", href: "/ogc/collections/x/items" }];

// Shape published by ogc-services/pygeoapi/cgis_ogc/publish.py for a ready raster.
function rasterCollection(id, { year, level = "level1", units = "people_per_cell", source = "CyberGIS Level 1 model", bbox = [68.18, 6.75, 97.41, 35.5], title } = {}) {
  return {
    id,
    title: title || `India Level 1 population ${year} (WorldPop redistributed by GHSL residential built-up surface)`,
    description: `Level 1 output (catalog version 1; native CRS EPSG:4326; units ${units}; NoData -99999; source ${source})`,
    keywords: ["raster", level, source, units, String(year)],
    links: tilesLink,
    extent: { spatial: { bbox: [bbox] } },
  };
}

test("description facts appended by the OGC publisher are parsed", () => {
  const { text, facts } = parseDescriptionFacts("Some text (catalog version 3; native CRS EPSG:32644; units people_per_cell; NoData -99999; source WorldPop (processed))");
  assert.equal(text, "Some text");
  assert.deepEqual(facts, { version: 3, crs: "EPSG:32644", units: "people_per_cell", nodata: -99999, source: "WorldPop (processed)" });
});

test("real Level 1 metadata (Agent 2 manifest: source label with parentheses) parses", () => {
  const src = "CyberGIS Level 1 model (cgis_level1) from WorldPop + GHSL GHS-BUILT-S R2023A";
  const c = {
    id: "india-l1-pop-2001",
    title: "India Level 1 population 2001 (WorldPop redistributed by GHSL residential built-up surface)",
    description: `Level 1 dasymetric redistribution: WorldPop unconstrained counts aggregated to 10x10-cell parents. (catalog version 1; native CRS EPSG:4326; units people_per_cell; NoData -99999; source ${src})`,
    keywords: ["raster", "level1", src, "people_per_cell", "2001"],
    links: tilesLink,
    extent: { spatial: { bbox: [[68.185416408, 6.753750174, 97.414582958, 35.501250059]] } },
  };
  const d = parseCollection(c);
  assert.deepEqual([d.kind, d.productLevel, d.year, d.units, d.crs, d.nodata, d.version, d.source], ["raster", "level1", 2001, "people_per_cell", "EPSG:4326", -99999, 1, src]);
  assert.ok(d.description.startsWith("Level 1 dasymetric"));
});

test("keywords are classified by value, not position (empty entries are dropped upstream)", () => {
  assert.deepEqual(parseKeywords(["raster", "level2", "people_per_cell", "2011"]), { kind: "raster", productLevel: "level2", units: "people_per_cell", year: 2011 });
  assert.deepEqual(parseKeywords(["vector", "other", "deterministic-fixture"]), { kind: "vector", productLevel: "other", source: "deterministic-fixture" });
});

test("eleven Level 1 years form one national time series, discovered from metadata only", () => {
  const collections = [];
  for (let y = 2001; y <= 2011; y++) collections.push(rasterCollection(`some-unexpected-name-${y * 7}`, { year: y }));
  const cat = buildCatalog({ collections, studyBbox: STUDY });
  assert.equal(cat.series.length, 1);
  const s = cat.series[0];
  assert.deepEqual(s.years, [2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011]);
  assert.equal(s.scope, "national");
  assert.equal(s.productLevel, "level1");
  assert.equal(s.title, "India Level 1 population (WorldPop redistributed by GHSL residential built-up surface)");
  assert.equal(s.byYear.get(2005), `some-unexpected-name-${2005 * 7}`);
  assert.ok(cat.nationalExtent);
});

test("Level 2 products group by provenance.group_id and are never treated as a time series", () => {
  const towns = ["a", "b", "c", "d", "e", "f"].map((t, i) => rasterCollection(`l2-${t}`, { year: 2011, level: "level2", bbox: [70 + i, 20, 70.1 + i, 20.1], title: `Level 2 town ${t}` }));
  const metadata = towns.map((c) => ({ dataset_id: c.id, group_id: "india-level2-2011", synthetic: false, license: "CC BY 4.0", resolution_x: 0.000211, resolution_y: 0.000211 }));
  const cat = buildCatalog({ collections: towns, metadata, studyBbox: STUDY });
  assert.equal(cat.series.length, 0);
  assert.equal(cat.level2Groups.length, 1);
  assert.equal(cat.level2Groups[0].id, "india-level2-2011");
  assert.equal(cat.level2Groups[0].members.length, 6);
  assert.equal(cat.byId.get("l2-a").provenance, "real");
  assert.equal(cat.byId.get("l2-a").license, "CC BY 4.0");
  assert.ok(Math.abs(approxCellMetres(cat.byId.get("l2-a")) - 22.1) < 2);
});

test("without group metadata, Level 2 falls back to one group per year", () => {
  const cat = buildCatalog({ collections: [rasterCollection("x", { year: 2011, level: "level2", bbox: [70, 20, 70.1, 20.1] })], studyBbox: STUDY });
  assert.equal(cat.level2Groups[0].id, "level2-2011");
  assert.equal(cat.level2Groups[0].declared, false);
});

test("provenance: SYNTHETIC markers, explicit flags, and unknown otherwise", () => {
  assert.equal(provenanceClass({ title: "SYNTHETIC population raster (2001)", source: "deterministic-fixture" }), "synthetic");
  assert.equal(provenanceClass({ title: "India Level 1 population 2001", synthetic: false }), "real");
  assert.equal(provenanceClass({ title: "India Level 1 population 2001", synthetic: null }), "unknown");
  assert.equal(provenanceClass({ title: "Anything", synthetic: true }), "synthetic");
});

test("Phase 2 fixtures: two source rasters form a 2-year series and the towns are a vector collection", () => {
  const collections = [
    { id: "synthetic-worldpop-2001", title: "SYNTHETIC population raster (2001)", description: "x (catalog version 1; native CRS EPSG:4326; units people_per_cell; NoData -9999; source deterministic-fixture)", keywords: ["raster", "source", "deterministic-fixture", "people_per_cell", "2001"], links: tilesLink, extent: { spatial: { bbox: [[78, 17.936, 78.064, 18]] } } },
    { id: "synthetic-worldpop-2011", title: "SYNTHETIC population raster (2011)", description: "x (catalog version 1; native CRS EPSG:4326; units people_per_cell; NoData -9999; source deterministic-fixture)", keywords: ["raster", "source", "deterministic-fixture", "people_per_cell", "2011"], links: tilesLink, extent: { spatial: { bbox: [[78, 17.936, 78.064, 18]] } } },
    { id: "synthetic-towns", title: "SYNTHETIC towns", description: "towns (catalog version 1; native CRS EPSG:4326; source deterministic-fixture)", keywords: ["vector", "source", "deterministic-fixture"], links: itemsLink, extent: { spatial: { bbox: [[78.01, 17.95, 78.035, 17.99]] } } },
  ];
  const cat = buildCatalog({ collections, studyBbox: STUDY });
  assert.equal(cat.rasters.length, 2);
  assert.equal(cat.vectors.length, 1);
  assert.equal(cat.series.length, 1);
  assert.equal(cat.nationalExtent, null);  // nothing national => camera may follow the data
  assert.deepEqual(cat.rasters.map((d) => d.id), ["synthetic-worldpop-2001", "synthetic-worldpop-2011"]);
  assert.equal(cat.byId.get("synthetic-worldpop-2001").nodata, -9999);
});

test("vendor TiTiler records fill structured gaps", () => {
  const c = { id: "r", title: "R", keywords: ["raster"], links: tilesLink };
  const cat = buildCatalog({ collections: [c], vendor: [{ dataset_id: "r", version: 4, crs: "EPSG:32644", units: "people_per_cell", nodata: -1, bbox: [1, 2, 3, 4], year: 2009 }] });
  const d = cat.byId.get("r");
  assert.deepEqual([d.version, d.crs, d.units, d.nodata, d.year], [4, "EPSG:32644", "people_per_cell", -1, 2009]);
  assert.deepEqual(d.bbox, [1, 2, 3, 4]);
});

test("pixel comparability: same grid ok, resolution and units differences flagged", () => {
  const l1 = { units: "people_per_cell", resolution: [0.000833, 0.000833], crs: "EPSG:4326", productLevel: "level1" };
  const l1b = { ...l1 };
  const l2 = { units: "people_per_cell", resolution: [0.000211, 0.000211], crs: "EPSG:4326", productLevel: "level2" };
  const dens = { ...l1, units: "people_per_km2" };
  assert.equal(pixelComparability(l1, l1b).ok, true);
  const r = pixelComparability(l1, l2);
  assert.equal(r.ok, false);
  assert.equal(r.reason, "resolution");
  assert.ok(r.ratio > 15 && r.ratio < 16);
  assert.equal(pixelComparability(l1, dens).reason, "units");
  assert.equal(pixelComparability({ ...l1, resolution: null }, { ...l2, resolution: null }).reason, "resolution");
});

test("scope and filter helpers", () => {
  assert.equal(scopeOf([68.18, 6.75, 97.41, 35.5], STUDY), "national");
  assert.equal(scopeOf([78, 17.9, 78.1, 18], STUDY), "local");
  const d = { id: "india-l1-pop-2005", title: "India Level 1 population 2005", year: 2005, source: "x", units: "people_per_cell", productLevel: "level1" };
  assert.ok(matchesFilter(d, "2005 level", "all"));
  assert.ok(!matchesFilter(d, "", "level2"));
  assert.ok(matchesFilter(d, "", "level1"));
});

test("parseCollection never invents a kind: tiles => raster, items => vector", () => {
  assert.equal(parseCollection({ id: "a", links: tilesLink }).kind, "raster");
  assert.equal(parseCollection({ id: "b", links: itemsLink }).kind, "vector");
});
