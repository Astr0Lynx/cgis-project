// Area of interest + server-side operations (zonal statistics, year series, Level 1 vs Level 2, clip).
// The browser never computes population values: every number comes from /api/operations/*. The only
// arithmetic here is the difference/ratio of two server totals over the same area, labelled as such.

import { $, h, icon, svg, replace, fmt, fmtSigned, humanUnits, stateCard } from "./dom.js";
import { request, toError, explain, isAbort } from "./http.js";
import { createDraw } from "./draw.js";
import { intersects, LEVEL_LABEL, approxCellMetres } from "./catalog.js";

const EMPTY = { type: "FeatureCollection", features: [] };
const SERIES_CONCURRENCY = 2;

function geomBbox(geom) {
  const xs = [];
  const ys = [];
  const walk = (c) => { if (typeof c[0] === "number") { xs.push(c[0]); ys.push(c[1]); } else c.forEach(walk); };
  walk(geom.coordinates);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function vertexCount(geom) {
  let n = 0;
  const walk = (c) => { if (typeof c[0] === "number") n += 1; else c.forEach(walk); };
  walk(geom.coordinates);
  return n;
}

export function createAnalysis(ctx) {
  const { map, store, out, OPERATIONS } = ctx;
  let busy = null;          // AbortController of the running operation
  let resultsGen = 0;

  const draw = createDraw(map, {
    onDone: (geometry, label) => { setDrawButtons(null); selectAoi(label, geometry, { source: "drawn" }); },
    onCancel: () => setDrawButtons(null),
  });

  function ensureLayers() {
    if (map.getSource("aoi")) return;
    map.addSource("aoi", { type: "geojson", data: EMPTY });
    map.addLayer({ id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": "#1f6feb", "fill-opacity": 0.06 } });
    map.addLayer({ id: "aoi-line", type: "line", source: "aoi", paint: { "line-color": "#1f6feb", "line-width": 2.5 } });
  }
  if (map.loaded()) ensureLayers(); else map.on("load", ensureLayers);

  // ---------------------------------------------------------------- AOI
  function selectAoi(label, geometry, { featureKey = "", source = "boundary", sub = "" } = {}) {
    ensureLayers();
    store.set({ aoi: { label, geometry, source, featureKey, bbox: geomBbox(geometry), vertices: vertexCount(geometry) } });
    ctx.vectors.setSelected(featureKey);
    // Drawn independently of the boundary layer so it survives viewport reloads of that layer.
    map.getSource("aoi").setData({ type: "Feature", properties: {}, geometry });
    $("aoi-card").classList.add("is-set");
    $("aoi-label").textContent = label;
    const a = store.get().aoi;
    $("aoi-sub").textContent = sub || `${source === "boundary" ? "Boundary" : source === "view" ? "Map view rectangle" : source === "search" ? "Search result" : "Drawn area"} · ${fmt(a.vertices)} vertices`;
    $("aoi-clear").hidden = false;
    refreshButtons();
  }
  ctx.selectAoi = selectAoi;

  function clearAoi() {
    store.set({ aoi: null });
    ctx.vectors.setSelected("");
    map.getSource("aoi")?.setData(EMPTY);
    $("aoi-card").classList.remove("is-set");
    $("aoi-label").textContent = "Click a boundary on the map, search for a place, or draw an area.";
    $("aoi-sub").textContent = "";
    $("aoi-clear").hidden = true;
    refreshButtons();
  }

  function clickSelect(point) {
    const layers = ctx.vectors.fillLayers();
    if (!layers.length) return;
    const hit = map.queryRenderedFeatures(point, { layers })[0];
    if (!hit) return;
    const key = hit.properties._key;
    const feature = ctx.vectors.featureByKey(key);
    if (!feature) return;
    const name = feature.properties.name || String(feature.id);
    const collection = key.slice(0, key.lastIndexOf("/"));
    const level = feature.properties.level ? ` · ${String(feature.properties.level).replaceAll("_", " ")}` : "";
    selectAoi(name, feature.geometry, { featureKey: key, source: "boundary", sub: `${collection}${level} · id ${feature.id}` });
    ctx.setTab("analysis");
  }

  function setDrawButtons(kind) {
    $("draw-rect").setAttribute("aria-pressed", kind === "rect" ? "true" : "false");
    $("draw-poly").setAttribute("aria-pressed", kind === "poly" ? "true" : "false");
  }
  $("draw-rect").addEventListener("click", () => { if (draw.mode() === "rect") { draw.cancel(); return; } draw.start("rect"); setDrawButtons("rect"); });
  $("draw-poly").addEventListener("click", () => { if (draw.mode() === "poly") { draw.cancel(); return; } draw.start("poly"); setDrawButtons("poly"); });
  $("aoi-view").addEventListener("click", () => {
    const b = map.getBounds();
    const ring = [[b.getWest(), b.getSouth()], [b.getEast(), b.getSouth()], [b.getEast(), b.getNorth()],
      [b.getWest(), b.getNorth()], [b.getWest(), b.getSouth()]].map((p) => [+p[0].toFixed(6), +p[1].toFixed(6)]);
    selectAoi("current map view", { type: "Polygon", coordinates: [ring] }, { source: "view" });
  });
  $("aoi-clear").addEventListener("click", clearAoi);

  // ---------------------------------------------------------------- buttons
  function level2For(aoi) {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    const B = s.mode !== "single" ? ctx.getDataset(s.b) : null;
    if (A?.productLevel === "level2") return A;
    if (B?.productLevel === "level2") return B;
    if (!aoi || !ctx.catalog) return null;
    const ids = ctx.catalog.level2Groups.flatMap((g) => g.members);
    return ids.map(ctx.getDataset).find((d) => d && intersects(d.bbox, aoi.bbox)) || null;
  }
  function level1For(l2) {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    const B = s.mode !== "single" ? ctx.getDataset(s.b) : null;
    if (A?.productLevel === "level1") return A;
    if (B?.productLevel === "level1") return B;
    const series = ctx.catalog?.series.find((x) => x.productLevel === "level1" && x.units === l2?.units);
    if (!series) return null;
    const year = l2?.year && series.byYear.has(l2.year) ? l2.year : series.years.at(-1);
    return ctx.getDataset(series.byYear.get(year));
  }

  function refreshButtons() {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    const ready = Boolean(s.aoi && A);
    $("zonal").disabled = !ready || Boolean(busy);
    $("clip").disabled = !ready || Boolean(busy);
    const series = ctx.display?.seriesOf(A);
    $("series").hidden = !series;
    if (series) {
      $("series").textContent = `Totals ${series.years[0]}–${series.years.at(-1)}`;
      $("series").disabled = !ready || Boolean(busy) || A.units !== "people_per_cell" && !/count/.test(A.units || "");
    }
    const hasL2 = Boolean(ctx.catalog?.level2Groups.length);
    $("l1l2").hidden = !hasL2;
    const l2 = hasL2 ? level2For(s.aoi) : null;
    $("l1l2").disabled = !s.aoi || !l2 || !level1For(l2) || Boolean(busy);
    let hint = "";
    if (!A) hint = "Choose a raster layer first.";
    else if (!s.aoi) hint = "Choose an area of interest to enable the operations.";
    else if (hasL2 && !l2) hint = "Level 1 vs Level 2 needs an area that overlaps a Level 2 town.";
    $("ops-hint").textContent = hint;
  }

  // ---------------------------------------------------------------- operations
  async function zonal(ds, aoi, signal) {
    const payload = { dataset_id: ds.id, aoi: aoi.geometry, aoi_crs: "EPSG:4326", operations: ["sum", "mean", "count"] };
    const res = await request(OPERATIONS.zonal, { method: "POST", json: payload, signal });
    out({ request: { ...payload, aoi: aoi.label }, http_status: res.status, response: res.body });
    if (!res.ok) throw toError(res, OPERATIONS.zonal);
    return res.body;
  }

  function begin() {
    busy?.abort();
    busy = new AbortController();
    refreshButtons();
    return busy;
  }
  function end(ctl) {
    if (busy === ctl) busy = null;
    refreshButtons();
  }

  async function runZonal() {
    const s = store.get();
    const aoi = s.aoi;
    const layers = [["A", ctx.getDataset(s.a)]];
    if (s.mode !== "single" && s.b && s.b !== s.a) layers.push(["B", ctx.getDataset(s.b)]);
    const ctl = begin();
    const gen = ++resultsGen;
    showRunning(`Computing zonal statistics for ${layers.length} layer${layers.length > 1 ? "s" : ""}…`, ctl);
    const results = [];
    for (const [slot, ds] of layers) {
      try {
        results.push({ slot, ds, body: await zonal(ds, aoi, ctl.signal) });
      } catch (err) {
        if (isAbort(err)) { end(ctl); return; }
        results.push({ slot, ds, err });
      }
    }
    end(ctl);
    if (gen !== resultsGen) return;
    const cards = results.map((r) => zonalCard(r, layers.length > 1));
    let delta = null;
    if (results.length === 2 && results.every((r) => r.body && r.body.sum_semantics === "total_count" && Number.isFinite(r.body.sum))) {
      const [a, b] = results;
      const d = b.body.sum - a.body.sum;
      const pct = a.body.sum ? (d / a.body.sum) * 100 : null;
      delta = h("div", { class: "result", "data-testid": "zonal-delta" },
        h("div", { class: "result-head" }, h("span", { class: "badge badge-b", text: "B" }), "−", h("span", { class: "badge badge-a", text: "A" }), h("span", { class: "grow", text: "Difference of the two server totals" })),
        h("div", { class: "hero", text: fmtSigned(d) }),
        h("p", { class: "hero-unit", text: `people${pct === null ? "" : ` · ${fmtSigned(pct, { digits: 1 })}% of A`}` }),
        h("p", { class: "note", text: "Both totals use the same area and the pixel-centre rule on each layer's native grid. Counts over the same area are comparable even when cell sizes differ." }),
        a.ds.provenance !== b.ds.provenance ? h("p", { class: "note-warn" }, icon("alert"), h("span", { text: "The layers have different provenance (synthetic vs real or unverified); this difference is not a real change." })) : null);
    }
    showResults(cards, delta);
  }

  function zonalCard({ slot, ds, body, err }, tagged) {
    const tag = tagged ? h("span", { class: `badge badge-${slot.toLowerCase()}`, text: slot }) : null;
    if (err) {
      return h("div", { class: "result", "data-testid": "zonal-error" },
        h("div", { class: "result-head" }, tag, h("span", { class: "grow", text: ds.title })),
        h("p", { class: "note-error" }, h("span", { text: explain(err) }), " ", h("code", { text: err.code })));
    }
    const isCount = body.sum_semantics === "total_count";
    const empty = body.count_valid === 0;
    const hero = empty ? "No valid cells" : isCount ? fmt(body.sum) : fmt(body.mean);
    const heroUnit = empty
      ? `All ${fmt(body.pixels_in_aoi)} selected cells are NoData; the server reports no total (not zero).`
      : isCount ? `people · sum of ${humanUnits(body.units)} over ${fmt(body.count_valid)} valid cells`
        : `mean ${humanUnits(body.units)} per cell (a sum is not meaningful for these units)`;
    return h("div", { class: "result", "data-testid": "zonal-result" },
      h("div", { class: "result-head" }, tag, h("span", { class: "grow", text: `${ds.title}${body.dataset_version ? ` · v${body.dataset_version}` : ""}` })),
      h("div", { class: "hero", "data-testid": "zonal-hero", text: hero }),
      h("p", { class: "hero-unit", text: heroUnit }),
      h("dl", { class: "meta" },
        h("dt", { text: "Valid cells" }), h("dd", { class: "num", text: fmt(body.count_valid) }),
        h("dt", { text: "NoData excluded" }), h("dd", { class: "num", text: `${fmt(body.nodata_excluded)}${body.nodata_value !== undefined && body.nodata_value !== null ? ` (value ${fmt(body.nodata_value)})` : ""}` }),
        isCount && !empty ? [h("dt", { text: "Mean per cell" }), h("dd", { class: "num", text: fmt(body.mean) })] : null,
        h("dt", { text: "Cell selection" }), h("dd", { text: body.pixel_selection?.rule === "pixel_center" ? "Pixel centre inside area (all_touched=false)" : body.pixel_selection?.rule || "—" }),
        h("dt", { text: "Raster CRS" }), h("dd", { text: body.raster_crs || "—" })),
      (body.warnings || []).map((w) => h("p", { class: "note-warn" }, icon("alert"), h("span", { text: typeof w === "string" ? w : w.message || JSON.stringify(w) }))));
  }

  // Year series for the active Level 1 (or any) series: one zonal call per year.
  async function runSeries() {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    const series = ctx.display.seriesOf(A);
    if (!series || !s.aoi) return;
    const aoi = s.aoi;
    const ctl = begin();
    const gen = ++resultsGen;
    const rows = series.years.map((year) => ({ year, id: series.byYear.get(year), status: "queued" }));
    let done = 0;
    const progress = () => showRunning(`Zonal statistics per year: ${done} of ${rows.length} done`, ctl, done / rows.length);
    progress();
    let next = 0;
    async function worker() {
      while (next < rows.length && !ctl.signal.aborted) {
        const row = rows[next++];
        try {
          const body = await zonal(ctx.getDataset(row.id), aoi, ctl.signal);
          Object.assign(row, { status: "ok", sum: body.sum_semantics === "total_count" ? body.sum : null, valid: body.count_valid, body });
        } catch (err) {
          if (isAbort(err)) return;
          Object.assign(row, { status: "error", err });
        }
        done += 1;
        if (gen === resultsGen) progress();
      }
    }
    await Promise.all(Array.from({ length: SERIES_CONCURRENCY }, worker));
    end(ctl);
    if (ctl.signal.aborted || gen !== resultsGen) {
      if (gen === resultsGen) showResults([h("p", { class: "note", text: `Stopped after ${done} of ${rows.length} years.` })]);
      return;
    }
    showResults([seriesCard(series, rows, aoi)]);
  }

  function seriesCard(series, rows, aoi) {
    const ok = rows.filter((r) => r.status === "ok" && Number.isFinite(r.sum));
    const errors = rows.filter((r) => r.status === "error");
    const tableId = "series-table";
    const toggle = h("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-controls": tableId, "aria-expanded": "false" }, "Show table");
    const table = h("table", { class: "data", id: tableId, hidden: true },
      h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Year" }), h("th", { scope: "col", text: "Total (people)" }), h("th", { scope: "col", text: "Valid cells" }))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { text: String(r.year) }),
        h("td", { text: r.status === "ok" ? fmt(r.sum) : r.status === "error" ? explain(r.err) : "—" }),
        h("td", { text: r.status === "ok" ? fmt(r.valid) : "—" })))));
    toggle.addEventListener("click", () => {
      table.hidden = !table.hidden;
      toggle.textContent = table.hidden ? "Show table" : "Hide table";
      toggle.setAttribute("aria-expanded", table.hidden ? "false" : "true");
    });
    const first = ok[0];
    const last = ok.at(-1);
    return h("div", { class: "result", "data-testid": "series-result" },
      h("div", { class: "result-head" }, h("span", { class: "grow", text: `${series.title} · ${aoi.label}` }), toggle),
      ok.length >= 2 ? [
        h("div", { class: "hero", text: fmtSigned(last.sum - first.sum) }),
        h("p", { class: "hero-unit", text: `people from ${first.year} to ${last.year} (difference of server totals: ${fmt(first.sum)} → ${fmt(last.sum)})` }),
        lineChart(ok),
        Math.max(...ok.map((p) => Math.abs(p.sum))) >= 1e5 ? h("p", { class: "note", text: "Axis: L = lakh (100,000), cr = crore (10,000,000). Numbers use Indian digit grouping." }) : null,
      ] : h("p", { class: "note", text: "Fewer than two years returned a total, so no trend is drawn." }),
      table,
      errors.length ? h("p", { class: "note-error", text: `${errors.length} year(s) failed: ${[...new Set(errors.map((e) => explain(e.err)))].join(" ")}` }) : null,
      h("p", { class: "note", text: "Each point is one zonal-statistics call on that year's raster. Years are not interpolated in the browser." }));
  }

  function lineChart(points) {
    const W = 320; const H = 150; const L = 52; const R = 10; const T = 10; const B = 22;
    const ys = points.map((p) => p.sum);
    let lo = Math.min(...ys); let hi = Math.max(...ys);
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.05 || 1;
    lo -= pad; hi += pad;
    const x = (i) => L + (i * (W - L - R)) / Math.max(1, points.length - 1);
    const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Line chart of totals from ${points[0].year} to ${points.at(-1).year}` });
    const grid = svg("g", { class: "grid" });
    const axis = svg("g", { class: "axis" });
    for (const v of [lo + pad, (lo + hi) / 2, hi - pad]) {
      grid.append(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }));
      const t = svg("text", { x: L - 6, y: y(v) + 3, "text-anchor": "end" });
      t.textContent = compact(v);
      axis.append(t);
    }
    points.forEach((p, i) => {
      if (points.length > 8 && i % 2 && i !== points.length - 1) return;
      const t = svg("text", { x: x(i), y: H - 6, "text-anchor": "middle" });
      t.textContent = String(p.year);
      axis.append(t);
    });
    const path = svg("path", { class: "line", d: points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.sum).toFixed(1)}`).join(" ") });
    const hair = svg("line", { class: "hair", y1: T, y2: H - B, visibility: "hidden" });
    root.append(grid, axis, hair, path);
    const tip = h("div", { class: "chart-tip", hidden: true });
    const wrap = h("div", { class: "chart" }, root, tip);
    const dots = points.map((p, i) => {
      const dot = svg("circle", { class: "dot", cx: x(i), cy: y(p.sum), r: 3.5 });
      root.append(dot);
      return dot;
    });
    const show = (i) => {
      const p = points[i];
      hair.setAttribute("x1", x(i)); hair.setAttribute("x2", x(i)); hair.setAttribute("visibility", "visible");
      dots.forEach((d, j) => d.setAttribute("r", j === i ? 5 : 3.5));
      replace(tip, h("strong", { text: fmt(p.sum) }), `${p.year} · people`);
      tip.hidden = false;
      const rect = root.getBoundingClientRect();
      tip.style.left = `${(x(i) / W) * rect.width}px`;
      tip.style.top = `${(y(p.sum) / H) * rect.height}px`;
    };
    const hide = () => { tip.hidden = true; hair.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("r", 3.5)); };
    points.forEach((p, i) => {
      const w = (W - L - R) / Math.max(1, points.length - 1);
      const hit = svg("rect", { class: "hit", x: x(i) - w / 2, y: T, width: w, height: H - T - B, tabindex: "0", role: "img", "aria-label": `${p.year}: ${fmt(p.sum)} people` });
      hit.addEventListener("pointerenter", () => show(i));
      hit.addEventListener("focus", () => show(i));
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("blur", hide);
      root.append(hit);
    });
    return wrap;
  }

  function compact(v) {
    const a = Math.abs(v);
    if (a >= 1e7) return `${(v / 1e7).toFixed(a >= 1e8 ? 0 : 1)} cr`;
    if (a >= 1e5) return `${(v / 1e5).toFixed(a >= 1e6 ? 0 : 1)} L`;
    if (a >= 1e3) return `${(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
    return fmt(v);
  }

  // Level 1 vs Level 2 on the same area: two server totals, each on its native grid.
  async function runL1L2() {
    const s = store.get();
    const aoi = s.aoi;
    const l2 = level2For(aoi);
    const l1 = level1For(l2);
    if (!aoi || !l1 || !l2) return;
    await Promise.all([ctx.display.ensureResolution(l1), ctx.display.ensureResolution(l2)]);
    const ctl = begin();
    const gen = ++resultsGen;
    showRunning("Computing Level 1 and Level 2 totals over the same area…", ctl);
    const results = [];
    for (const ds of [l1, l2]) {
      try { results.push({ ds, body: await zonal(ds, aoi, ctl.signal) }); } catch (err) {
        if (isAbort(err)) { end(ctl); return; }
        results.push({ ds, err });
      }
    }
    end(ctl);
    if (gen !== resultsGen) return;
    const beyond = l2.bbox && (aoi.bbox[0] < l2.bbox[0] || aoi.bbox[1] < l2.bbox[1] || aoi.bbox[2] > l2.bbox[2] || aoi.bbox[3] > l2.bbox[3]);
    const rows = results.map(({ ds, body, err }) => {
      const m = approxCellMetres(ds);
      return h("tr", {},
        h("td", {}, h("strong", { text: LEVEL_LABEL[ds.productLevel] || ds.productLevel }), h("br"), h("span", { class: "hint", text: `${ds.year ?? ""}${m ? ` · ≈ ${fmt(Math.round(m))} m cells` : ""}` })),
        h("td", { text: err ? explain(err) : body.sum_semantics === "total_count" ? fmt(body.sum) : "n/a" }),
        h("td", { text: err ? "—" : fmt(body.count_valid) }));
    });
    const [a, b] = results;
    const both = a.body && b.body && Number.isFinite(a.body.sum) && Number.isFinite(b.body.sum);
    showResults([h("div", { class: "result", "data-testid": "l1l2-result" },
      h("div", { class: "result-head" }, h("span", { class: "grow", text: `Level 1 vs Level 2 · ${aoi.label}` })),
      both ? [h("div", { class: "hero", text: fmtSigned(b.body.sum - a.body.sum) }),
        h("p", { class: "hero-unit", text: `people, Level 2 minus Level 1${a.body.sum ? ` (${fmtSigned(((b.body.sum - a.body.sum) / a.body.sum) * 100, { digits: 1 })}%)` : ""}` })] : null,
      h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Product" }), h("th", { scope: "col", text: "Total (people)" }), h("th", { scope: "col", text: "Valid cells" }))),
        h("tbody", {}, rows)),
      beyond ? h("p", { class: "note-warn" }, icon("alert"), h("span", { text: "The area extends beyond the Level 2 town extent, so Level 2 covers only part of it. Select the town boundary for a like-for-like comparison." })) : null,
      h("p", { class: "note", text: "Common support: the same polygon, pixel-centre rule, each product on its own native grid. Raw cell values are not compared because the cells differ in size; only totals over the area are." }))]);
  }

  async function runClip() {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    const payload = { dataset_id: A.id, aoi: s.aoi.geometry, aoi_crs: "EPSG:4326", format: "GTiff" };
    const ctl = begin();
    replace($("download"), h("span", { class: "hint", text: "Clipping…" }));
    try {
      const res = await request(OPERATIONS.clip, { method: "POST", json: payload, signal: ctl.signal });
      out({ request: { ...payload, aoi: s.aoi.label }, http_status: res.status, response: res.body });
      if (!res.ok) throw toError(res, OPERATIONS.clip);
      const body = res.body;
      if (body.job_id && body.status === "completed") {
        const r = body.result || {};
        replace($("download"),
          h("a", { href: OPERATIONS.download(body.job_id), class: "btn btn-secondary btn-block", "data-testid": "download-link" }, icon("download"), "Download clipped GeoTIFF"),
          h("span", { class: "hint", style: { display: "block", marginTop: "4px" }, text: `${r.filename || ""}${r.output ? ` · ${r.output.width}×${r.output.height} cells · ${r.output.crs}` : ""}` }));
      } else {
        replace($("download"), h("span", { class: "hint", text: `Clip job ${body.status}.` }));
      }
    } catch (err) {
      if (!isAbort(err)) replace($("download"), h("span", { class: "note-error", text: `${explain(err)} (${err.code})` }));
    } finally {
      end(ctl);
    }
  }

  function showRunning(text, ctl, fraction = null) {
    replace($("results"), h("div", { class: "result", "data-testid": "op-running" },
      h("div", { class: "result-head" }, h("span", { class: "status status-running", text: "running" }), h("span", { class: "grow", text })),
      h("div", { class: `progress${fraction === null ? " is-indeterminate" : ""}`, role: "progressbar", "aria-label": text,
        "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": fraction === null ? undefined : String(Math.round(fraction * 100)) },
      h("span", { style: { width: `${Math.round((fraction ?? 0.3) * 100)}%` } })),
      h("button", { class: "btn btn-secondary btn-sm", type: "button", "data-testid": "op-cancel", onclick: () => ctl.abort() }, "Cancel")));
  }

  function showResults(cards, extra) {
    replace($("results"), cards, extra || null);
    $("results-section").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  $("zonal").addEventListener("click", () => runZonal());
  $("series").addEventListener("click", () => runSeries());
  $("l1l2").addEventListener("click", () => runL1L2());
  $("clip").addEventListener("click", () => runClip());

  store.subscribe((s, changed) => {
    if (changed.includes("a") || changed.includes("b") || changed.includes("mode")) refreshButtons();
  });

  replace($("results"), stateCard({ title: "No results yet", text: "Choose an area, then run zonal statistics. Totals are computed by the processing service, never in the browser." }));

  return {
    selectAoi, clearAoi, clickSelect, refreshButtons,
    isDrawing: () => draw.active(),
  };
}
