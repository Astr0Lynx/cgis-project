// Lightweight AOI drawing on MapLibre: rectangle (two clicks) or polygon (click vertices; finish with
// a double-click, Enter, or a click on the first vertex). Esc cancels, Backspace removes a vertex.

import { $, h, replace } from "./dom.js";

const EMPTY = { type: "FeatureCollection", features: [] };

export function createDraw(map, { onDone, onCancel }) {
  let mode = null;   // "rect" | "poly" | null
  let points = [];
  let hover = null;

  function ensureLayers() {
    if (map.getSource("draw")) return;
    map.addSource("draw", { type: "geojson", data: EMPTY });
    map.addLayer({ id: "draw-fill", type: "fill", source: "draw", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#1f6feb", "fill-opacity": 0.12 } });
    map.addLayer({ id: "draw-line", type: "line", source: "draw", paint: { "line-color": "#1f6feb", "line-width": 2, "line-dasharray": [2, 1.5] } });
    map.addLayer({ id: "draw-pts", type: "circle", source: "draw", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#1f6feb", "circle-stroke-width": 2 } });
  }

  function rectRing(a, b) {
    return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]], [a[0], a[1]]];
  }

  function redraw() {
    const feats = points.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }));
    if (mode === "rect" && points.length === 1 && hover) {
      feats.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [rectRing(points[0], hover)] } });
    } else if (mode === "poly" && points.length) {
      const ring = hover ? [...points, hover] : points;
      if (ring.length >= 3) feats.push({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } });
      else feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } });
    }
    map.getSource("draw")?.setData({ type: "FeatureCollection", features: feats });
  }

  function hint(text) {
    const el = $("draw-hint");
    if (!text) { el.hidden = true; return; }
    el.hidden = false;
    replace(el, h("span", { text }), h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => cancel() }, "Cancel (Esc)"));
  }

  function start(kind) {
    ensureLayers();
    mode = kind;
    points = [];
    hover = null;
    map.doubleClickZoom.disable();
    map.getCanvas().style.cursor = "crosshair";
    hint(kind === "rect" ? "Click one corner, then the opposite corner." : "Click to add vertices. Double-click, press Enter or click the first point to finish.");
    redraw();
  }

  function stop() {
    mode = null;
    points = [];
    hover = null;
    map.getSource("draw")?.setData(EMPTY);
    map.doubleClickZoom.enable();
    map.getCanvas().style.cursor = "";
    hint(null);
  }

  function cancel() {
    const was = mode;
    stop();
    if (was) onCancel?.();
  }

  function finish(geometry, label) {
    stop();
    onDone(geometry, label);
  }

  function finishPoly() {
    if (points.length < 3) return;
    finish({ type: "Polygon", coordinates: [[...points, points[0]].map((p) => [+p[0].toFixed(6), +p[1].toFixed(6)])] }, `drawn polygon (${points.length} vertices)`);
  }

  map.on("click", (e) => {
    if (!mode) return;
    e.drawConsumed = true;   // other click handlers (inspector, boundary pick) must ignore this click
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (mode === "rect") {
      if (!points.length) { points.push(p); redraw(); return; }
      const a = points[0];
      if (a[0] === p[0] || a[1] === p[1]) return;
      const sw = [Math.min(a[0], p[0]), Math.min(a[1], p[1])];
      const ne = [Math.max(a[0], p[0]), Math.max(a[1], p[1])];
      finish({ type: "Polygon", coordinates: [rectRing(sw, ne).map((q) => [+q[0].toFixed(6), +q[1].toFixed(6)])] }, "drawn rectangle");
      return;
    }
    if (points.length >= 3) {
      const first = map.project(points[0]);
      if (Math.hypot(first.x - e.point.x, first.y - e.point.y) < 10) { finishPoly(); return; }
    }
    points.push(p);
    redraw();
  });
  map.on("dblclick", (e) => {
    if (mode !== "poly") return;
    e.preventDefault();
    points.pop(); // the second click of the double-click added a duplicate vertex
    finishPoly();
  });
  map.on("mousemove", (e) => {
    if (!mode) return;
    hover = [e.lngLat.lng, e.lngLat.lat];
    redraw();
  });
  window.addEventListener("keydown", (e) => {
    if (!mode) return;
    if (e.key === "Escape") { e.preventDefault(); cancel(); }
    else if (e.key === "Enter" && mode === "poly") { e.preventDefault(); finishPoly(); }
    else if (e.key === "Backspace" && points.length && !(e.target instanceof HTMLInputElement)) { e.preventDefault(); points.pop(); redraw(); }
  });

  return { start, cancel, active: () => mode !== null, mode: () => mode };
}
