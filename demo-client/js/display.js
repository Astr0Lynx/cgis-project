// What is drawn and how it is coloured: layer A (+ B in compare modes), Level 2 overlays, legends,
// the year timeline, comparison notes and the provenance badge.

import { $, h, icon, replace, fmt, humanUnits } from "./dom.js";
import { getJson } from "./http.js";
import { rasterSlot } from "./rasters.js";
import { parseStyle, sharedRange, rampCss, sameStyle } from "./legend.js";
import { PROVENANCE_LABEL, LEVEL_LABEL, pixelComparability, approxCellMetres } from "./catalog.js";

const MAX_OVERLAYS = 60;

export function createDisplay(ctx) {
  const { map, store, TILES } = ctx;
  const styleCache = new Map();
  const statsCache = new Map();
  const infoCache = new Map();
  const tileErrors = new Map();
  let generation = 0;
  let current = { setA: [], styles: new Map() };
  let overlaySlots = [];
  let markers = [];
  let playing = 0;

  const firstAbove = () => overlaySlots[0]?.layerId && map.getLayer(overlaySlots[0].layerId)
    ? overlaySlots[0].layerId : ctx.vectors.firstLayerId() || (map.getLayer("l2-extents") ? "l2-extents" : (map.getLayer("aoi-fill") ? "aoi-fill" : undefined));
  const slotA = rasterSlot(map, { sourceId: "raster", layerId: "raster", before: firstAbove });

  const get = (id) => ctx.getDataset(id);
  const seriesOf = (ds) => (ds && ds.seriesKey ? ctx.catalog.series.find((s) => s.key === ds.seriesKey) : null);

  function styleOf(id) {
    if (!styleCache.has(id)) {
      styleCache.set(id, ctx.tilesetMeta(id).then((m) => parseStyle(m.style)).catch(() => null));
    }
    return styleCache.get(id);
  }
  function statsOf(id) {
    if (!statsCache.has(id)) {
      statsCache.set(id, getJson(`${TILES}/datasets/${encodeURIComponent(id)}/statistics`).catch((err) => { statsCache.delete(id); throw err; }));
    }
    return statsCache.get(id);
  }

  /** Native cell size via the vendor /info route when the catalog does not expose it (display only). */
  async function ensureResolution(ds) {
    if (!ds || ds.resolution || ds.kind !== "raster") return ds;
    if (!infoCache.has(ds.id)) {
      infoCache.set(ds.id, getJson(`${TILES}/datasets/${encodeURIComponent(ds.id)}/info`).catch(() => null));
    }
    const info = await infoCache.get(ds.id);
    if (info && Array.isArray(info.bounds) && info.width && info.height) {
      const [w, s, e, n] = info.bounds;
      ds.resolution = [Math.abs(e - w) / info.width, Math.abs(n - s) / info.height];
    }
    return ds;
  }

  /** Layers allowed to share A's colour scale: same units and the same product level / support. */
  function sharesScale(a, b) {
    if (!a || !b || !a.units || a.units !== b.units) return false;
    const pc = pixelComparability(a, b);
    if (a.resolution && b.resolution) return pc.ok;
    return a.productLevel === b.productLevel;
  }

  function scaleSet(ds, other) {
    const ids = new Set();
    const add = (d) => { const s = seriesOf(d); if (s) s.ids.forEach((i) => ids.add(i)); else ids.add(d.id); };
    add(ds);
    if (other && sharesScale(ds, other)) add(other);
    return [...ids];
  }

  async function renderFor(ds, set, styles, s) {
    const own = styles.get(ds.id) || null;
    if (s.scale === "shared") {
      const allOgc = set.every((id) => styles.get(id)?.shared && sameStyle(styles.get(id), own));
      if (allOgc && s.src === "ogc") return { source: "ogc", legend: { ...own, how: "ogc-configured", across: set.length } };
      try {
        const range = sharedRange(await Promise.all(set.map(statsOf)));
        if (range) return { source: "vendor", rescale: [range.lo, range.hi], colormap: "viridis", legend: { ...range, how: "titiler-shared", across: set.length } };
      } catch { /* fall through to the published style */ }
    }
    if (s.src === "ogc") return { source: "ogc", legend: own ? { ...own, how: own.shared ? "ogc-configured" : "ogc-own" } : { how: "unknown" } };
    if (own) return { source: "vendor", rescale: [own.lo, own.hi], colormap: own.colormap, legend: { ...own, how: "titiler-published" } };
    try {
      const r = sharedRange([await statsOf(ds.id)]);
      if (r) return { source: "vendor", rescale: [r.lo, r.hi], colormap: "viridis", legend: { ...r, how: "titiler-own" } };
    } catch { /* unknown range */ }
    return { source: "vendor", legend: { how: "unknown" } };
  }

  async function refresh() {
    const gen = ++generation;
    const s = store.get();
    const cat = ctx.catalog;
    const A = get(s.a);
    const B = s.mode !== "single" ? get(s.b) : null;
    renderEmpty(cat, A);
    renderBadge(A, B);
    if (!A) {
      slotA.apply(null);
      ctx.compare.applyB(null);
      clearOverlays();
      replace($("legend"));
      $("timeline").hidden = true;
      return;
    }
    if (B) await Promise.all([ensureResolution(A), ensureResolution(B)]);
    if (gen !== generation) return;
    const setA = scaleSet(A, B);
    const setB = B ? (setA.includes(B.id) ? setA : scaleSet(B, null)) : [];
    const ids = [...new Set([...setA, ...setB])];
    const styleList = await Promise.all(ids.map(styleOf));
    if (gen !== generation) return;
    const styles = new Map(ids.map((id, i) => [id, styleList[i]]));
    current = { setA, setB, styles };
    const [rA, rB] = await Promise.all([renderFor(A, setA, styles, s), B ? renderFor(B, setB, styles, s) : null]);
    if (gen !== generation) return;
    const [rangeA, rangeB] = await Promise.all([ctx.tileRange(A.id, rA.source), B ? ctx.tileRange(B.id, rB.source) : null]);
    if (gen !== generation) return;

    tileErrors.clear();
    slotA.apply({ tiles: [ctx.tileUrl(A.id, rA.source, rA)], ...rangeA, opacity: s.opacity, visible: s.visible });
    ctx.compare.applyB(B ? { tiles: [ctx.tileUrl(B.id, rB.source, rB)], ...rangeB, opacity: s.opacity, visible: s.visible } : null);
    await applyOverlays(A, s, gen);
    if (gen !== generation) return;

    const legends = [];
    const varied = setA.length > 1 && s.scale !== "shared" && !setA.every((id) => sameStyle(styles.get(id), styles.get(A.id)));
    if (B && setA.includes(B.id) && rA.legend.how === rB.legend.how && rA.legend.lo === rB.legend.lo && rA.legend.hi === rB.legend.hi) {
      legends.push({ slot: "A + B", ds: A, r: rA, varied });
    } else {
      legends.push({ slot: B ? "A" : null, ds: A, r: rA, varied });
      if (B) legends.push({ slot: "B", ds: B, r: rB, varied: setB.length > 1 && s.scale !== "shared" && !setB.every((id) => sameStyle(styles.get(id), styles.get(B.id))) });
    }
    if (overlaySlots.length) legends.push(overlayLegend);
    renderLegends(legends, s);
    renderTimeline(A, B);
    renderCompareNotes(A, B, s);
  }

  // ---------------------------------------------------------------- Level 2 overlays
  let overlayLegend = null;
  function clearOverlays() {
    for (const slot of overlaySlots) slot.remove();
    overlaySlots = [];
    overlayLegend = null;
    for (const m of markers) m.remove();
    markers = [];
    if (map.getLayer("l2-extents")) map.removeLayer("l2-extents");
    if (map.getSource("l2-extents")) map.removeSource("l2-extents");
  }

  async function applyOverlays(A, s, gen) {
    const groups = ctx.catalog.level2Groups;
    const showOverlay = s.overlayL2 && A.productLevel !== "level2";
    const showMarkers = showOverlay || A.productLevel === "level2";
    const members = groups.flatMap((g) => g.members).slice(0, MAX_OVERLAYS).map(get).filter(Boolean);
    clearOverlays();
    if (!members.length || !showMarkers) return;
    const features = members.filter((d) => d.bbox).map((d) => ({
      type: "Feature", properties: { id: d.id, title: d.title },
      geometry: { type: "Polygon", coordinates: [[[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[1]], [d.bbox[2], d.bbox[3]], [d.bbox[0], d.bbox[3]], [d.bbox[0], d.bbox[1]]]] },
    }));
    map.addSource("l2-extents", { type: "geojson", data: { type: "FeatureCollection", features } });
    map.addLayer({ id: "l2-extents", type: "line", source: "l2-extents",
      paint: { "line-color": "#17202b", "line-width": 1.2, "line-dasharray": [2, 2], "line-opacity": 0.7 } },
    map.getLayer("aoi-fill") ? "aoi-fill" : undefined);
    for (const d of members) {
      if (!d.bbox) continue;
      const el = h("button", { class: "town-marker", type: "button", title: `${d.title} — zoom to this Level 2 town`, "data-testid": "l2-marker" },
        d.title.replace(/^.*?level\s*2[^,:–-]*[,:–-]\s*/i, "") || d.title);
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]], { padding: 60, maxZoom: 15 });
      });
      markers.push(new maplibregl.Marker({ element: el, anchor: "bottom" })
        .setLngLat([(d.bbox[0] + d.bbox[2]) / 2, d.bbox[3]]).addTo(map));
    }
    if (!showOverlay) return;
    const styles = await Promise.all(members.map((d) => styleOf(d.id)));
    if (gen !== generation) return;
    let render;
    if (s.scale === "shared") {
      try {
        const range = sharedRange(await Promise.all(members.map((d) => statsOf(d.id))));
        if (range) render = { source: "vendor", rescale: [range.lo, range.hi], colormap: "viridis", legend: { ...range, how: "titiler-shared", across: members.length } };
      } catch { /* own styles */ }
      if (gen !== generation) return;
    }
    const ranges = await Promise.all(members.map((d) => ctx.tileRange(d.id, render ? render.source : "ogc")));
    if (gen !== generation) return;
    members.forEach((d, i) => {
      const slot = rasterSlot(map, { sourceId: `l2-${i}`, layerId: `l2-${i}`, before: () => ctx.vectors.firstLayerId() || (map.getLayer("l2-extents") ? "l2-extents" : undefined) });
      slot.apply({ tiles: [ctx.tileUrl(d.id, render ? "vendor" : "ogc", render || {})], ...ranges[i], opacity: Math.min(1, s.opacity + 0.1), visible: s.visible });
      overlaySlots.push(slot);
    });
    const first = styles[0];
    const sameAll = styles.every((st) => sameStyle(st, first));
    overlayLegend = {
      slot: "L2", ds: members[0], overlay: true, title: `Level 2 overlay · ${members.length} town${members.length === 1 ? "" : "s"}`,
      r: render || { source: "ogc", legend: first ? { ...first, how: first.shared ? "ogc-configured" : "ogc-own" } : { how: "unknown" } },
      varied: !render && !sameAll,
    };
  }

  // ---------------------------------------------------------------- legends
  const HOW = {
    "ogc-configured": "OGC API – Tiles style · configured shared range",
    "ogc-own": "OGC API – Tiles style · this dataset's own min–max",
    "titiler-shared": (n) => (n > 1 ? `Shared range across ${n} layers · TiTiler rendering (vendor)` : "Range from this layer's statistics · TiTiler rendering (vendor)"),
    "titiler-published": "Published range · TiTiler rendering (vendor)",
    "titiler-own": "This layer's range · TiTiler rendering (vendor)",
    unknown: "The server did not publish a colour range for this layer.",
  };

  function renderLegends(list, s) {
    // Side by side: B's legend sits in B's pane so neither map centre is covered.
    const side = s.mode === "side";
    const hostB = $("legend-b");
    const cards = list.map((item) => [item, legendCard(item, s)]);
    replace($("legend"), cards.filter(([item]) => !(side && item.slot === "B")).map(([, el]) => el));
    replace(hostB, side ? cards.filter(([item]) => item.slot === "B").map(([, el]) => el) : null);
    hostB.hidden = !side || !hostB.children.length;
  }

  function legendCard(item, s) {
    {
      const L = item.r.legend || {};
      const how = typeof HOW[L.how] === "function" ? HOW[L.how](L.across || 1) : HOW[L.how] || "";
      const ds = item.ds;
      const nodata = ds.nodata !== null && ds.nodata !== undefined ? `NoData (${fmt(ds.nodata)}) is transparent` : "NoData cells are transparent";
      const hasRange = Number.isFinite(L.lo) && Number.isFinite(L.hi);
      const tag = item.slot === "A" ? h("span", { class: "badge badge-a", text: "A" })
        : item.slot === "B" ? h("span", { class: "badge badge-b", text: "B" })
          : item.slot === "A + B" ? [h("span", { class: "badge badge-a", text: "A" }), h("span", { class: "badge badge-b", text: "B" })]
            : item.slot === "L2" ? h("span", { class: "badge badge-level", text: "L2" }) : null;
      const err = tileErrors.get(item.slot === "B" ? "raster-b" : item.slot === "L2" ? "l2" : "raster");
      return h("section", { class: "overlay legend", "aria-label": `Legend ${item.slot || ""}`.trim(), "data-testid": "legend-card" },
        h("p", { class: "legend-title" }, tag, h("span", { class: "grow", text: item.title || `${humanUnits(ds.units)}${item.slot === "A + B" ? " · shared" : ""}` })),
        hasRange ? [
          h("div", { class: "legend-ramp", style: { background: rampCss(L.colormap) }, role: "img",
            "aria-label": `${L.colormap || "viridis"} colour ramp from ${fmt(L.lo)} to ${fmt(L.hi)} ${humanUnits(ds.units)}` }),
          h("div", { class: "legend-ticks", "data-testid": "legend-ticks" },
            h("span", { text: fmt(L.lo) }), h("span", { text: fmt((L.lo + L.hi) / 2) }), h("span", { text: `${L.clipped ? "≥ " : ""}${fmt(L.hi)}` })),
        ] : null,
        h("p", { class: "legend-nodata" }, h("i", { "aria-hidden": "true" }), h("span", { text: nodata })),
        h("p", { class: "legend-scale", "data-testid": "legend-scale", text: how }),
        item.varied ? h("div", { class: "note-warn", "data-testid": "legend-not-comparable" }, icon("alert"), h("div", {},
          h("span", { text: item.overlay ? "Each town uses its own range, so colours are not comparable between towns." : "Each year/layer uses its own range, so the same colour can mean different values." }),
          item.overlay ? null : h("button", { class: "btn btn-secondary btn-sm", type: "button", "data-testid": "use-shared",
            onclick: () => store.set({ scale: "shared", src: ogcStylesShared() ? s.src : "vendor" }) }, "Use a shared range"))) : null,
        err ? h("p", { class: "note-error", text: "Some tiles failed to load for this layer." }) : null);
    }
  }

  function reportTileError(sourceId) {
    const key = sourceId === "raster" || sourceId === "raster-b" ? sourceId : sourceId.startsWith("l2-") ? "l2" : null;
    if (!key || tileErrors.has(key)) return;
    tileErrors.set(key, true);
    const card = [...document.querySelectorAll("#legend .legend, #legend-b .legend")].find((el) => {
      const label = el.getAttribute("aria-label") || "";
      return key === "raster-b" ? label.endsWith("B") : key === "l2" ? label.endsWith("L2") : !label.endsWith("B") && !label.endsWith("L2");
    });
    card?.append(h("p", { class: "note-error", text: "Some tiles failed to load for this layer." }));
  }

  function ogcStylesShared() {
    const { setA, styles } = current;
    if (!setA?.length) return false;
    const first = styles.get(setA[0]);
    return setA.every((id) => styles.get(id)?.shared && sameStyle(styles.get(id), first));
  }

  // ---------------------------------------------------------------- timeline
  function renderTimeline(A, B) {
    const host = $("timeline");
    const series = seriesOf(A);
    if (!series || series.years.length < 2) { host.hidden = true; stopPlay(); return; }
    host.hidden = false;
    const idx = series.years.indexOf(A.year);
    const bYear = B && B.seriesKey === series.key ? B.year : null;
    const range = h("input", { type: "range", id: "year-slider", min: "0", max: String(series.years.length - 1), step: "1", value: String(idx),
      "aria-label": "Year (layer A)", "aria-valuetext": String(A.year), "data-testid": "year-slider" });
    range.addEventListener("input", () => {
      const year = series.years[Number(range.value)];
      store.set({ a: series.byYear.get(year) });
    });
    const play = h("button", { class: "icon-btn", type: "button", id: "year-play", "aria-label": playing ? "Pause animation" : "Play years", "aria-pressed": playing ? "true" : "false" },
      icon(playing ? "pause" : "play"));
    play.addEventListener("click", () => (playing ? stopPlay() : startPlay(series)));
    replace(host,
      h("div", { class: "timeline-head" }, play, h("strong", { text: String(A.year), "data-testid": "timeline-year" }),
        h("span", { text: `${series.title} · ${series.years.length} years${bYear ? ` · B = ${bYear}` : ""}` })),
      range,
      h("div", { class: "timeline-ticks", "aria-hidden": "true" }, series.years.map((y) => h("span", {
        text: series.years.length > 12 && y !== series.years[0] && y !== series.years.at(-1) && y % 5 ? "·" : String(y),
        style: y === bYear ? { color: "var(--compare-b)", fontWeight: "700" } : y === A.year ? { color: "var(--ink)", fontWeight: "700" } : null,
      }))));
    if (document.activeElement?.id === "year-slider") requestAnimationFrame(() => $("year-slider")?.focus());
  }

  function startPlay(series) {
    stopPlay();
    playing = setInterval(() => {
      const A = get(store.get().a);
      if (!A || A.seriesKey !== series.key) { stopPlay(); return; }
      const i = (series.years.indexOf(A.year) + 1) % series.years.length;
      store.set({ a: series.byYear.get(series.years[i]) });
    }, 1600);
    renderTimeline(get(store.get().a), store.get().mode !== "single" ? get(store.get().b) : null);
  }
  function stopPlay() {
    if (!playing) return;
    clearInterval(playing);
    playing = 0;
    const btn = $("year-play");
    if (btn) { replace(btn, icon("play")); btn.setAttribute("aria-label", "Play years"); btn.setAttribute("aria-pressed", "false"); }
  }

  // ---------------------------------------------------------------- comparison notes
  function renderCompareNotes(A, B, s) {
    const host = $("compare-notes");
    $("layer-b-field").hidden = s.mode === "single";
    $("slot-a-tag").hidden = s.mode === "single";
    $("compare-summary").textContent = s.mode === "single" ? "Off" : s.mode === "swipe" ? "Swipe" : "Side by side";
    if (s.mode === "single") { replace(host); return; }
    if (!B) { replace(host, h("p", { class: "hint", text: "Choose layer B to compare." })); return; }
    const notes = [];
    const pc = pixelComparability(A, B);
    if (pc.ok) {
      notes.push(h("p", { class: "hint", "data-testid": "compare-ok", text: s.scale === "shared"
        ? "Same units and cell size, drawn on one shared colour range: colours and pixel values are directly comparable."
        : "Same units and cell size: pixel values are comparable. Switch to a shared range so colours are comparable too." }));
    } else if (pc.reason === "units") {
      notes.push(h("p", { class: "note-warn", "data-testid": "compare-units" }, icon("alert"),
        h("span", { text: `Different units (${humanUnits(A.units)} vs ${humanUnits(B.units)}): values cannot be compared and each layer keeps its own legend.` })));
    } else {
      const ma = approxCellMetres(A);
      const mb = approxCellMetres(B);
      const sizes = ma && mb ? ` A cells ≈ ${fmt(Math.round(ma))} m, B cells ≈ ${fmt(Math.round(mb))} m (≈ ${fmt(Math.round((Math.max(ma, mb) / Math.min(ma, mb)) ** 2))}× the area).` : "";
      notes.push(h("p", { class: "note-warn", "data-testid": "compare-resolution" }, icon("alert"),
        h("span", { text: `Raw pixel values are not directly comparable: the layers use different cell sizes.${sizes} Each keeps its own legend. Compare totals over a common area instead (Analysis → Level 1 vs Level 2).` })));
    }
    if (A.provenance !== B.provenance) {
      notes.push(h("p", { class: "note-warn" }, icon("alert"),
        h("span", { text: `A is ${PROVENANCE_LABEL[A.provenance].toLowerCase()} and B is ${PROVENANCE_LABEL[B.provenance].toLowerCase()}. Do not read the difference as a real change.` })));
    }
    replace(host, h("div", { style: { marginTop: "8px" } }, notes));
  }

  // ---------------------------------------------------------------- provenance badge + empty state
  function badge(ds, slot) {
    const cls = ds.provenance;
    return h("span", { class: `badge badge-${cls === "real" ? "real" : cls === "synthetic" ? "synthetic" : "unknown"}`, title: `${slot ? `Layer ${slot}: ` : ""}${PROVENANCE_LABEL[cls]}`, "data-provenance": cls },
      icon(cls === "real" ? "check" : cls === "synthetic" ? "flask" : "help"), `${slot ? `${slot} · ` : ""}${PROVENANCE_LABEL[cls]}`);
  }
  function renderBadge(A, B) {
    const host = $("provenance-badge");
    if (!A) { replace(host); return; }
    replace(host, B && B.provenance !== A.provenance ? [badge(A, "A"), " ", badge(B, "B")] : badge(A));
  }

  function renderEmpty(cat, A) {
    const host = $("map-empty");
    if (!cat || A || store.get().catalogStatus === "loading") { host.hidden = true; return; }
    host.hidden = false;
    const admin = ctx.auth?.isAdmin();
    replace(host, h("div", { class: "state-card" },
      h("h4", { text: "No raster layers are published yet" }),
      h("p", { text: "The OGC API lists no collections with tiles. Once a dataset is imported or published it appears here automatically." }),
      admin ? h("a", { class: "btn btn-primary btn-sm", href: "#/admin" }, "Upload a dataset") : null));
  }

  function applyPaint() {
    const s = store.get();
    slotA.setOpacity(s.opacity);
    slotA.setVisible(s.visible);
    for (const slot of overlaySlots) { slot.setOpacity(Math.min(1, s.opacity + 0.1)); slot.setVisible(s.visible); }
    ctx.compare.applyPaint(s.opacity, s.visible);
  }

  function defaultB(aId) {
    const A = get(aId);
    if (!A) return null;
    const series = seriesOf(A);
    if (series) {
      const other = series.years[0] === A.year ? series.years.at(-1) : series.years[0];
      return series.byYear.get(other);
    }
    const alt = ctx.catalog.rasters.find((d) => d.id !== aId && d.units === A.units) || ctx.catalog.rasters.find((d) => d.id !== aId);
    return alt ? alt.id : null;
  }

  return {
    refresh, applyPaint, reportTileError, ogcStylesShared, defaultB, ensureResolution, seriesOf, statsOf,
    LEVEL_LABEL,
  };
}
