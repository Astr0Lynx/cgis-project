// One raster layer slot on a MapLibre map. Keeps the layer when only the tile URL changes (year
// switches), so the previous tiles stay on screen until new ones arrive instead of flashing to blank.

export function rasterSlot(map, { sourceId, layerId, before }) {
  let range = null; // "minzoom|maxzoom|bounds" of the live source

  const rangeKey = (spec) => `${spec.minzoom ?? ""}|${spec.maxzoom ?? ""}|${(spec.bounds || []).join(",")}`;

  function remove() {
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
    range = null;
  }

  /** spec: {tiles, minzoom, maxzoom, bounds, opacity, visible} or null to clear. */
  function apply(spec) {
    if (!spec) { remove(); return; }
    const key = rangeKey(spec);
    const src = map.getSource(sourceId);
    if (src && range === key && typeof src.setTiles === "function") {
      if (src.tiles?.[0] !== spec.tiles[0]) src.setTiles(spec.tiles);
    } else {
      remove();
      const source = { type: "raster", tiles: spec.tiles, tileSize: 256 };
      for (const k of ["minzoom", "maxzoom", "bounds"]) if (spec[k] !== undefined) source[k] = spec[k];
      map.addSource(sourceId, source);
      const beforeId = typeof before === "function" ? before() : before;
      map.addLayer({
        id: layerId,
        type: "raster",
        source: sourceId,
        layout: { visibility: spec.visible === false ? "none" : "visible" },
        paint: { "raster-opacity": spec.opacity ?? 0.8, "raster-resampling": "nearest", "raster-fade-duration": 120 },
      }, beforeId && map.getLayer(beforeId) ? beforeId : undefined);
      range = key;
    }
    setOpacity(spec.opacity ?? 0.8);
    setVisible(spec.visible !== false);
  }

  function setOpacity(v) {
    if (map.getLayer(layerId)) map.setPaintProperty(layerId, "raster-opacity", v);
  }
  function setVisible(v) {
    if (map.getLayer(layerId)) map.setLayoutProperty(layerId, "visibility", v ? "visible" : "none");
  }

  return { apply, remove, setOpacity, setVisible, layerId, sourceId };
}
