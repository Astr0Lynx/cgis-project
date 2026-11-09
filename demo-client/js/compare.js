// Layer B map for swipe / side-by-side comparison. Both maps share one camera; B only draws raster B
// and the AOI outline. The swipe divider is a keyboard-operable slider.

import { $ } from "./dom.js";
import { rasterSlot } from "./rasters.js";

export function createCompare(ctx) {
  const { map, store } = ctx;
  let mapB = null;
  let slotB = null;
  let pendingSpec = null;
  let syncing = false;
  let swipePct = 50;

  function ensureMap() {
    if (mapB) return mapB;
    mapB = new maplibregl.Map({
      container: "map-b",
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#dde3e1" } }] },
      center: map.getCenter(),
      zoom: map.getZoom(),
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
    });
    mapB.touchZoomRotate.disableRotation();
    window.mapB = mapB;
    slotB = rasterSlot(mapB, { sourceId: "raster-b", layerId: "raster-b", before: () => (mapB.getLayer("aoi-b-line") ? "aoi-b-line" : undefined) });
    mapB.on("load", () => {
      mapB.addSource("aoi-b", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      mapB.addLayer({ id: "aoi-b-line", type: "line", source: "aoi-b", paint: { "line-color": "#1f6feb", "line-width": 3 } });
      syncAoi();
      if (pendingSpec !== null) { slotB.apply(pendingSpec); }
    });
    mapB.on("error", (e) => { if (e && e.sourceId) ctx.display.reportTileError(e.sourceId); });
    mapB.on("click", (e) => {
      if (ctx.analysis.isDrawing()) return;
      ctx.inspector?.query(e.lngLat);
    });
    const mirror = (from, to) => () => {
      if (syncing) return;
      syncing = true;
      to.jumpTo({ center: from.getCenter(), zoom: from.getZoom(), bearing: from.getBearing(), pitch: from.getPitch() });
      syncing = false;
    };
    map.on("move", mirror(map, mapB));
    mapB.on("move", mirror(mapB, map));
    return mapB;
  }

  function setMode(mode) {
    const stage = $("map-stage");
    const paneB = $("pane-b");
    stage.classList.toggle("is-swipe", mode === "swipe");
    stage.classList.toggle("is-side", mode === "side");
    paneB.hidden = mode === "single";
    $("swipe-handle").hidden = mode !== "swipe";
    $("tag-a").hidden = mode === "single";
    $("tag-b").hidden = mode === "single";
    if (mode !== "single") {
      ensureMap();
      applyClip();
    } else {
      paneB.style.clipPath = "";
    }
    requestAnimationFrame(() => {
      map.resize();
      if (mapB) { mapB.resize(); mapB.jumpTo({ center: map.getCenter(), zoom: map.getZoom() }); }
    });
    updateTags();
  }

  function applyClip() {
    const paneB = $("pane-b");
    const handle = $("swipe-handle");
    if (store.get().mode === "swipe") {
      paneB.style.clipPath = `inset(0 0 0 ${swipePct}%)`;
      handle.style.left = `${swipePct}%`;
      handle.querySelector("button").setAttribute("aria-valuenow", String(Math.round(swipePct)));
    } else {
      paneB.style.clipPath = "";
    }
  }

  // Divider: pointer drag + arrow keys.
  const handleBtn = $("swipe-handle").querySelector("button");
  handleBtn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handleBtn.setPointerCapture(e.pointerId);
    const rect = $("map-stage").getBoundingClientRect();
    const move = (ev) => { swipePct = Math.min(98, Math.max(2, ((ev.clientX - rect.left) / rect.width) * 100)); applyClip(); };
    const up = () => { handleBtn.removeEventListener("pointermove", move); handleBtn.removeEventListener("pointerup", up); };
    handleBtn.addEventListener("pointermove", move);
    handleBtn.addEventListener("pointerup", up);
  });
  handleBtn.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 2;
    if (e.key === "ArrowLeft") swipePct = Math.max(2, swipePct - step);
    else if (e.key === "ArrowRight") swipePct = Math.min(98, swipePct + step);
    else if (e.key === "Home") swipePct = 2;
    else if (e.key === "End") swipePct = 98;
    else return;
    e.preventDefault();
    applyClip();
  });

  function applyB(spec) {
    pendingSpec = spec;
    updateTags();
    if (!mapB || !slotB) return;
    if (!mapB.loaded() && !mapB.isStyleLoaded()) return;  // applied on load
    slotB.apply(spec);
  }

  function applyPaint(opacity, visible) {
    if (!slotB) return;
    slotB.setOpacity(opacity);
    slotB.setVisible(visible);
  }

  function updateTags() {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    const B = ctx.getDataset(s.b);
    $("tag-a").textContent = A ? `A · ${A.title}` : "A";
    $("tag-b").textContent = B ? `B · ${B.title}` : "B";
  }

  function syncAoi() {
    if (!mapB || !mapB.getSource("aoi-b")) return;
    const aoi = store.get().aoi;
    mapB.getSource("aoi-b").setData(aoi ? { type: "Feature", properties: {}, geometry: aoi.geometry } : { type: "FeatureCollection", features: [] });
  }
  store.subscribe((s, changed) => { if (changed.includes("aoi")) syncAoi(); });

  return {
    setMode,
    applyB,
    applyPaint,
    resize: () => mapB?.resize(),
    getMap: () => mapB,
  };
}
