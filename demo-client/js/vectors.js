// Boundary collections (OGC API - Features): map layers, viewport loading, and the Boundaries panel.

import { $, h, icon, replace, fmt, debounce } from "./dom.js";
import { getJson, explain } from "./http.js";
import { ViewportLoader } from "./boundaries.js";

const EMPTY = { type: "FeatureCollection", features: [] };

export function createVectors(ctx) {
  const { map, OGC, ORIGIN, config } = ctx;
  const entries = new Map();      // collection id -> {ds, loader, visible, state}
  const featureIndex = new Map(); // "<collection>/<feature id>" -> GeoJSON feature (full geometry)
  let selectedKey = "";

  const ids = (id) => ({ src: `v-${id}`, fill: `v-${id}-fill`, casing: `v-${id}-casing`, line: `v-${id}-line`, selected: `v-${id}-selected` });

  function removeAll() {
    for (const [id, e] of entries) {
      e.loader.abort();
      const l = ids(id);
      for (const layer of [l.selected, l.line, l.casing, l.fill]) if (map.getLayer(layer)) map.removeLayer(layer);
      if (map.getSource(l.src)) map.removeSource(l.src);
    }
    entries.clear();
    featureIndex.clear();
  }

  async function init(datasets) {
    removeAll();
    for (const ds of datasets) {
      const l = ids(ds.id);
      map.addSource(l.src, { type: "geojson", data: EMPTY });
      const before = map.getLayer("aoi-fill") ? "aoi-fill" : undefined;
      map.addLayer({ id: l.fill, type: "fill", source: l.src, paint: { "fill-color": "#17202b", "fill-opacity": 0.04 } }, before);
      map.addLayer({ id: l.casing, type: "line", source: l.src, paint: { "line-color": "#ffffff", "line-width": 3, "line-opacity": 0.7 } }, before);
      map.addLayer({ id: l.line, type: "line", source: l.src, paint: { "line-color": "#2b3440", "line-width": 1.1 } }, before);
      map.addLayer({ id: l.selected, type: "line", source: l.src, filter: ["==", ["get", "_key"], selectedKey],
        paint: { "line-color": "#1f6feb", "line-width": 3.5 } }, before);
      map.on("mouseenter", l.fill, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", l.fill, () => { map.getCanvas().style.cursor = ""; });
      const entry = { ds, visible: true, state: { status: "idle" } };
      entry.loader = new ViewportLoader({
        base: OGC, collectionId: ds.id, extent: ds.bbox, fetchJson: getJson, origin: ORIGIN,
        options: config.boundaries, onState: (st) => onState(ds.id, st),
      });
      entries.set(ds.id, entry);
    }
    renderPanel();
    await refresh();
  }

  function viewBbox() {
    const b = map.getBounds();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  }

  function refresh() {
    const bbox = viewBbox();
    return Promise.all([...entries.values()].filter((e) => e.visible).map((e) => e.loader.load(bbox)));
  }

  function onState(id, st) {
    const e = entries.get(id);
    if (!e) return;
    e.state = st;
    const src = map.getSource(ids(id).src);
    if (st.status === "ok") {
      for (const f of st.features) {
        f.properties = { ...(f.properties || {}), _key: `${id}/${f.id}` };
        featureIndex.set(f.properties._key, f);
      }
      src?.setData({ type: "FeatureCollection", features: st.features });
    } else if (st.status !== "loading") {
      src?.setData(EMPTY);
    }
    renderRow(id);
  }

  function setVisible(id, visible) {
    const e = entries.get(id);
    if (!e) return;
    e.visible = visible;
    const l = ids(id);
    for (const layer of [l.fill, l.casing, l.line, l.selected]) {
      if (map.getLayer(layer)) map.setLayoutProperty(layer, "visibility", visible ? "visible" : "none");
    }
    if (visible) e.loader.load(viewBbox());
    else { e.loader.abort(); e.state = { status: "hidden" }; renderRow(id); }
  }

  function setSelected(key) {
    selectedKey = key || "";
    for (const id of entries.keys()) {
      const layer = ids(id).selected;
      if (map.getLayer(layer)) map.setFilter(layer, ["==", ["get", "_key"], selectedKey]);
    }
  }

  function stateLine(e) {
    const st = e.state || {};
    const budget = config.boundaries.maxFeaturesPerView;
    switch (st.status) {
      case "loading": return { text: "Loading features in view…" };
      case "ok": return { text: `${fmt(st.features.length)} feature${st.features.length === 1 ? "" : "s"} in view${st.pages > 1 ? ` · ${st.pages} pages` : ""}` };
      case "too_many":
      case "budget": return { text: `${fmt(st.numberMatched)} features in this view exceed the ${fmt(budget)}-feature limit. Zoom in to draw them.`, cls: "is-warn" };
      case "outside": return { text: "Outside the current view." };
      case "empty": return { text: "No features in this view." };
      case "error": return { text: `Could not load: ${explain(st.error)}`, cls: "is-error" };
      case "hidden": return { text: "Hidden." };
      default: return { text: "" };
    }
  }

  function renderRow(id) {
    const e = entries.get(id);
    const row = document.querySelector(`[data-vector-row="${CSS.escape(id)}"] .boundary-state`);
    if (!e || !row) return;
    const line = stateLine(e);
    row.textContent = line.text;
    row.className = `boundary-state${line.cls ? ` ${line.cls}` : ""}`;
  }

  function renderPanel() {
    const host = $("vectors");
    if (!entries.size) {
      replace(host, h("p", { class: "hint", text: "No boundary collections are published." }));
      return;
    }
    replace(host, [...entries.values()].map((e) => {
      const box = h("input", { type: "checkbox", checked: e.visible ? true : null });
      box.addEventListener("change", () => setVisible(e.ds.id, box.checked));
      const zoom = h("button", { class: "icon-btn", type: "button", title: "Zoom to collection extent", "aria-label": `Zoom to ${e.ds.title}`,
        onclick: () => e.ds.bbox && map.fitBounds([[e.ds.bbox[0], e.ds.bbox[1]], [e.ds.bbox[2], e.ds.bbox[3]]], { padding: 30 }) }, icon("zoom"));
      return h("div", { class: "boundary-row", "data-vector-row": e.ds.id },
        h("label", { class: "check" }, box, h("span", {}, h("span", { text: e.ds.title }), h("span", { class: "boundary-state" }))),
        zoom);
    }));
    for (const id of entries.keys()) renderRow(id);
  }

  map.on("moveend", debounce(() => { if (entries.size) refresh(); }, 250));

  return {
    init,
    refresh,
    setSelected,
    featureByKey: (key) => featureIndex.get(key),
    fillLayers: () => [...entries.keys()].filter((id) => entries.get(id).visible).map((id) => ids(id).fill).filter((l) => map.getLayer(l)),
    firstLayerId: () => { const first = entries.keys().next(); return first.done ? undefined : ids(first.value).fill; },
    collections: () => [...entries.values()].map((e) => e.ds),
    loadedFeatures: () => [...featureIndex.values()],
    rememberFeature: (key, f) => featureIndex.set(key, f),
  };
}
