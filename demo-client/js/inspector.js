// Pixel readout at a clicked location via TiTiler /point (vendor-specific route, labelled as such).
// Values are shown exactly as the server returns them; NoData is reported, never shown as zero.

import { $, h, icon, replace, fmt, fmtSigned, humanUnits } from "./dom.js";
import { getJson, explain } from "./http.js";
import { pixelComparability } from "./catalog.js";

export function createInspector(ctx) {
  const { store, TILES } = ctx;
  const host = $("inspector");
  let generation = 0;

  const inside = (b, ll) => !b || (ll.lng >= b[0] && ll.lng <= b[2] && ll.lat >= b[1] && ll.lat <= b[3]);

  async function point(ds, ll) {
    if (!inside(ds.bbox, ll)) return { status: "outside" };
    const url = `${TILES}/datasets/${encodeURIComponent(ds.id)}/point/${ll.lng.toFixed(6)},${ll.lat.toFixed(6)}`;
    try {
      const body = await getJson(url);
      const values = body.values ?? [];
      const v = values[0];
      const nodata = v === null || v === undefined || !Number.isFinite(v) || (ds.nodata !== null && ds.nodata !== undefined && v === ds.nodata);
      return { status: nodata ? "nodata" : "ok", value: v, raw: values };
    } catch (err) {
      return { status: "error", err };
    }
  }

  async function query(ll) {
    const s = store.get();
    const A = ctx.getDataset(s.a);
    if (!A) return;
    const B = s.mode !== "single" ? ctx.getDataset(s.b) : null;
    const gen = ++generation;
    host.hidden = false;
    replace(host, head(ll), h("p", { class: "hint", text: "Reading pixel values…" }));
    const layers = [["A", A], ...(B ? [["B", B]] : [])];
    const results = await Promise.all(layers.map(([, ds]) => point(ds, ll)));
    if (gen !== generation) return;
    const rows = layers.map(([slot, ds], i) => row(slot, ds, results[i], Boolean(B)));
    let delta = null;
    if (B && results[0].status === "ok" && results[1].status === "ok") {
      const pc = pixelComparability(A, B);
      delta = pc.ok
        ? h("div", { class: "delta", "data-testid": "pixel-delta" }, h("span", { text: "B − A" }), h("strong", { text: fmtSigned(results[1].value - results[0].value) }), h("span", { class: "unit", text: humanUnits(A.units) }))
        : h("p", { class: "note", text: pc.reason === "units" ? "Different units: no difference is shown." : "Different cell sizes: raw values are not directly comparable, so no difference is shown." });
    }
    const raw = layers.map(([slot], i) => `${slot} ${results[i].raw ? JSON.stringify(results[i].raw) : results[i].status}`).join(" · ");
    replace(host, head(ll), rows, delta,
      h("p", { id: "pixel", text: `TiTiler /point (vendor-specific): ${raw}` }));
  }

  function head(ll) {
    return h("div", { class: "inspector-head" },
      icon("target"), h("strong", { text: "Pixel value" }),
      h("span", { class: "grow", text: `${ll.lat.toFixed(4)}° N, ${ll.lng.toFixed(4)}° E` }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close pixel readout", onclick: () => { host.hidden = true; } }, icon("x")));
  }

  function row(slot, ds, r, tagged) {
    const tag = tagged ? h("span", { class: `badge badge-${slot.toLowerCase()}`, text: slot }) : null;
    let body;
    if (r.status === "ok") body = [h("span", { class: "val", text: fmt(r.value) }), h("span", { class: "unit", text: humanUnits(ds.units) })];
    else if (r.status === "nodata") body = h("span", { class: "unit", text: `NoData${ds.nodata !== null && ds.nodata !== undefined ? ` (${fmt(ds.nodata)})` : ""} — no value here` });
    else if (r.status === "outside") body = h("span", { class: "unit", text: "Outside this layer's extent" });
    else body = h("span", { class: "unit", text: explain(r.err) });
    return h("div", { class: "inspector-row", "data-testid": `pixel-${slot.toLowerCase()}` }, tag, body);
  }

  // Keep the legacy element present before the first click (tests and screen readers look for it).
  replace(host, h("p", { id: "pixel", text: "Pixel value: click the map." }));

  return { query };
}
