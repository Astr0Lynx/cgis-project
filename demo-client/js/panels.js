// Dataset browser (grouped by product level, time series, Level 2 group), layer selects and the
// active-layer summary. All labels come from catalog metadata via textContent.

import { $, h, icon, replace, fmt, humanUnits, skeleton, stateCard } from "./dom.js";
import { explain } from "./http.js";
import { LEVEL_LABEL, PROVENANCE_LABEL, PRODUCT_LEVELS, matchesFilter, approxCellMetres } from "./catalog.js";

export function createPanels(ctx) {
  const { store } = ctx;
  const filter = { text: "", level: "all" };
  const openDetails = new Set();

  const cat = () => ctx.catalog;
  const get = (id) => ctx.getDataset(id);

  $("catalog-filter").addEventListener("input", (e) => { filter.text = e.target.value.trim(); renderCatalog(); });

  function renderLoading() {
    for (const id of ["raster", "raster-b"]) {
      replace($(id), h("option", { value: "", text: "Loading layers…" }));
      $(id).disabled = true;
    }
    replace($("catalog"), skeleton(4));
    replace($("vectors"), skeleton(2));
    $("catalog-count").textContent = "";
  }

  function renderDiscoveryError(err, retry) {
    const action = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: retry }, "Try again");
    replace($("catalog"), stateCard({ title: "Could not reach the OGC API", text: `${explain(err)} (GET /ogc/collections)`, kind: "error", action }));
    replace($("vectors"));
    for (const id of ["raster", "raster-b"]) replace($(id), h("option", { value: "", text: "Unavailable" }));
    const empty = $("map-empty");
    empty.hidden = false;
    replace(empty, stateCard({ title: "The map cannot load its layers", text: explain(err), kind: "error",
      action: h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: retry }, "Try again") }));
  }

  function optionGroups(list) {
    const groups = new Map();
    for (const d of list) {
      const series = d.seriesKey ? cat().series.find((s) => s.key === d.seriesKey) : null;
      const label = d.productLevel === "level2" ? `${LEVEL_LABEL.level2} · selected towns`
        : series ? `${LEVEL_LABEL[d.productLevel] || d.productLevel} · ${series.title}` : LEVEL_LABEL[d.productLevel] || d.productLevel;
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(d);
    }
    return [...groups].map(([label, items]) => h("optgroup", { label },
      items.map((d) => h("option", { value: d.id, text: d.title }))));
  }

  function fillSelects() {
    const s = store.get();
    const rasters = cat().rasters;
    for (const [id, value] of [["raster", s.a], ["raster-b", s.b]]) {
      const sel = $(id);
      if (!rasters.length) {
        replace(sel, h("option", { value: "", text: "No raster layers published" }));
        sel.disabled = true;
        continue;
      }
      replace(sel, optionGroups(rasters));
      sel.disabled = false;
      sel.value = value || "";
    }
  }

  function provenanceBadge(ds, small = false) {
    const cls = ds.provenance;
    return h("span", { class: `badge badge-${cls === "real" ? "real" : cls === "synthetic" ? "synthetic" : "unknown"}`, title: PROVENANCE_LABEL[cls] },
      icon(cls === "real" ? "check" : cls === "synthetic" ? "flask" : "help"), small ? (cls === "real" ? "Real" : cls === "synthetic" ? "SYNTHETIC" : "Unverified") : PROVENANCE_LABEL[cls]);
  }

  function renderActive() {
    const A = get(store.get().a);
    const host = $("active-meta");
    if (!A) { replace(host, h("span", { text: cat() ? "No layer selected." : "" })); return; }
    const m = approxCellMetres(A);
    replace(host,
      provenanceBadge(A, true),
      h("span", { class: "badge badge-level", text: LEVEL_LABEL[A.productLevel] || A.productLevel }),
      A.year ? h("span", { class: "num", text: String(A.year) }) : null,
      h("span", { text: humanUnits(A.units) }),
      m ? h("span", { text: `≈ ${fmt(Math.round(m))} m cells` }) : null,
      A.version ? h("span", { text: `v${A.version}` }) : null);
  }

  function metaList(ds) {
    const m = approxCellMetres(ds);
    const missing = (text) => h("span", { class: "muted", text });
    const row = (k, v) => [h("dt", { text: k }), h("dd", {}, v ?? missing("—"))];
    return h("dl", { class: "meta", "data-testid": "dataset-metadata" },
      row("ID", h("span", { class: "mono", text: ds.id })),
      row("Version", ds.version ? `v${ds.version}` : null),
      row("Year", ds.year ? String(ds.year) : null),
      row("Level", LEVEL_LABEL[ds.productLevel] || ds.productLevel),
      row("Units", ds.units ? `${ds.units} (${humanUnits(ds.units)})` : missing("not declared")),
      row("CRS", ds.crs),
      row("NoData", ds.nodata !== null && ds.nodata !== undefined ? fmt(ds.nodata) : missing("not declared")),
      row("Cell size", m ? `≈ ${fmt(Math.round(m))} m${ds.resolution ? ` (${ds.resolution.map((r) => +r.toPrecision(4)).join(" × ")} ${/^EPSG:4326$/i.test(ds.crs || "") ? "°" : "units"})` : ""}` : missing("open the layer to measure")),
      row("Source", ds.source),
      row("License", ds.license || missing("not exposed by the public API — see dataset provenance")),
      row("Provenance", provenanceBadge(ds)),
      row("Extent", ds.bbox ? `${ds.bbox.map((v) => v.toFixed(3)).join(", ")} (lon/lat)` : null),
      row("Metadata", ds.metadataSource.map((s) => ({ ogc: "OGC API", tiles: "TiTiler (vendor)", catalog: "catalog API" }[s])).join(" + ")));
  }

  function detailsToggle(ds) {
    const open = openDetails.has(ds.id);
    const btn = h("button", { class: "icon-btn", type: "button", "aria-expanded": open ? "true" : "false", "aria-label": `Metadata for ${ds.title}`, title: "Metadata" }, icon("info"));
    btn.addEventListener("click", async () => {
      if (openDetails.has(ds.id)) openDetails.delete(ds.id); else openDetails.add(ds.id);
      renderCatalog();
      if (openDetails.has(ds.id) && !ds.resolution && ds.kind === "raster") {
        await ctx.display.ensureResolution(ds);
        renderCatalog();
      }
    });
    return btn;
  }

  function useAs(ds, slot) {
    store.set(slot === "B" ? { b: ds.id } : { a: ds.id });
  }

  function dsItem(ds) {
    const s = store.get();
    const compare = s.mode !== "single";
    const zoom = ds.bbox ? h("button", { class: "icon-btn", type: "button", title: "Zoom to extent", "aria-label": `Zoom to ${ds.title}`,
      onclick: () => ctx.map.fitBounds([[ds.bbox[0], ds.bbox[1]], [ds.bbox[2], ds.bbox[3]]], { padding: 40 }) }, icon("zoom")) : null;
    return h("li", { class: `ds-item${ds.id === s.a ? " is-active" : ""}${compare && ds.id === s.b ? " is-b" : ""}`, "data-dataset": ds.id },
      h("div", { class: "ds-main" },
        h("button", { class: "ds-title", type: "button", text: ds.title, title: `Show ${ds.title} as layer A`, onclick: () => useAs(ds, "A") }),
        h("span", { class: "ds-sub" }, [ds.year, humanUnits(ds.units), ds.scope === "national" ? "nationwide" : "local extent"].filter(Boolean).join(" · ")),
        openDetails.has(ds.id) ? h("div", { class: "ds-details" }, metaList(ds)) : null),
      h("div", { class: "ds-actions" },
        compare ? h("button", { class: "btn btn-ghost btn-sm", type: "button", title: "Use as layer B", "aria-label": `Use ${ds.title} as layer B`, onclick: () => useAs(ds, "B") }, "B") : null,
        zoom, detailsToggle(ds)));
  }

  function seriesCard(series) {
    const s = store.get();
    const compare = s.mode !== "single";
    const members = series.ids.map(get);
    const A = get(s.a);
    const active = A && A.seriesKey === series.key;
    const focus = active ? A : members[members.length - 1];
    const m = approxCellMetres(focus);
    return h("div", { class: `series-card${active ? " is-active" : ""}`, "data-testid": "series-card", "data-series": series.key },
      h("div", { class: "series-top" },
        h("p", { class: "series-title", text: series.title }),
        provenanceBadge(focus, true),
        detailsToggle(focus)),
      h("p", { class: "series-meta", text: [LEVEL_LABEL[series.productLevel] || series.productLevel,
        `${series.years.length} years ${series.years[0]}–${series.years.at(-1)}`, humanUnits(series.units),
        m ? `≈ ${fmt(Math.round(m))} m` : null, series.scope === "national" ? "nationwide" : "local extent"].filter(Boolean).join(" · ") }),
      h("div", { class: "year-grid", role: "group", "aria-label": `${series.title}: choose year for layer A${compare ? " (Shift+click for layer B)" : ""}` },
        series.years.map((year) => {
          const id = series.byYear.get(year);
          const btn = h("button", { class: `year-btn${compare && id === s.b ? " is-b" : ""}`, type: "button", "aria-pressed": id === s.a ? "true" : "false",
            "data-year": String(year), title: compare ? `${year}: click for A, Shift+click for B` : String(year), text: String(year) });
          btn.addEventListener("click", (e) => store.set(e.shiftKey && compare ? { b: id } : { a: id }));
          return btn;
        })),
      openDetails.has(focus.id) ? h("div", { class: "ds-details" }, metaList(focus)) : null);
  }

  function level2Card(group) {
    const s = store.get();
    const members = group.members.map(get).filter((d) => matchesFilter(d, filter.text, filter.level));
    if (!members.length) return null;
    const box = h("input", { type: "checkbox", id: "l2-overlay", checked: s.overlayL2 ? true : null, "data-testid": "l2-overlay" });
    box.addEventListener("change", () => store.set({ overlayL2: box.checked }));
    const m = approxCellMetres(members[0]);
    return h("div", { class: "group", "data-testid": "level2-group", "data-group": group.id },
      h("div", { class: "group-head" }, h("h3", { text: `${LEVEL_LABEL.level2} · selected towns${group.year ? ` · ${group.year}` : ""}` }), h("span", { text: `${group.members.length} town${group.members.length === 1 ? "" : "s"}` })),
      h("p", { class: "group-note", text: `${group.declared ? `Logical group ${group.id}. ` : ""}${m ? `≈ ${fmt(Math.round(m))} m target grid` : "Fine target grid"} per town; regridding categorical land-use inputs does not create observed detail at that resolution.` }),
      h("label", { class: "check", style: { margin: "0 0 8px" } }, box, "Overlay every town on the map"),
      h("ul", { class: "ds-list" }, members.map(dsItem)));
  }

  function renderFacets() {
    const host = $("catalog-facets");
    const counts = new Map();
    for (const d of cat().rasters) counts.set(d.productLevel, (counts.get(d.productLevel) || 0) + 1);
    const levels = PRODUCT_LEVELS.filter((l) => counts.has(l));
    if (levels.length < 2) { replace(host); filter.level = "all"; return; }
    replace(host, ["all", ...levels].map((l) => {
      const chip = h("button", { class: "chip", type: "button", "aria-pressed": filter.level === l ? "true" : "false",
        text: `${l === "all" ? "All" : LEVEL_LABEL[l]} ${l === "all" ? cat().rasters.length : counts.get(l)}` });
      chip.addEventListener("click", () => { filter.level = l; renderFacets(); renderCatalog(); });
      return chip;
    }));
  }

  function renderCatalog() {
    const c = cat();
    if (!c) return;
    const host = $("catalog");
    $("catalog-count").textContent = `${c.rasters.length} raster${c.rasters.length === 1 ? "" : "s"} · ${c.vectors.length} boundary set${c.vectors.length === 1 ? "" : "s"}`;
    if (!c.rasters.length) {
      replace(host, stateCard({ title: "No raster datasets published", text: "Collections with tiles appear here automatically once imported or published by an administrator." }));
      return;
    }
    const sections = [];
    const inSeries = new Set();
    const seriesByLevel = new Map();
    for (const s of c.series) {
      s.ids.forEach((id) => inSeries.add(id));
      if (!s.ids.some((id) => matchesFilter(get(id), filter.text, filter.level))) continue;
      if (!seriesByLevel.has(s.productLevel)) seriesByLevel.set(s.productLevel, []);
      seriesByLevel.get(s.productLevel).push(s);
    }
    for (const level of PRODUCT_LEVELS) {
      const blocks = [];
      for (const s of seriesByLevel.get(level) || []) blocks.push(seriesCard(s));
      if (level === "level2") {
        for (const g of c.level2Groups) { const card = level2Card(g); if (card) sections.push(card); }
      }
      const singles = c.rasters.filter((d) => d.productLevel === level && !inSeries.has(d.id) && d.productLevel !== "level2" && matchesFilter(d, filter.text, filter.level));
      if (singles.length) blocks.push(h("ul", { class: "ds-list" }, singles.map(dsItem)));
      if (!blocks.length) continue;
      sections.push(h("div", { class: "group", "data-level": level },
        h("div", { class: "group-head" }, h("h3", { text: LEVEL_LABEL[level] || level })),
        blocks));
    }
    replace(host, sections.length ? sections : stateCard({ title: "No datasets match", text: "Clear the filter to see every published layer." }));
  }

  function renderAll() {
    fillSelects();
    renderFacets();
    renderCatalog();
    renderActive();
  }

  function renderAccess() {
    if (cat()) ctx.display.refresh();
  }

  return { renderLoading, renderDiscoveryError, renderAll, renderCatalog, renderActive, renderAccess };
}
