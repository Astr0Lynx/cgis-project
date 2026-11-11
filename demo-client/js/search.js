// Place search over the published boundary collections (OGC API - Features).
//
// Server-side: prefix search with CQL2 `LIKE` only when /conformance advertises the advanced comparison
// operators; otherwise the standard queryable parameter `name=` (exact match). Client-side: substring
// matches among boundaries already loaded for the current view. Never downloads a whole collection.

import { $, h, replace, debounce } from "./dom.js";
import { getJson, explain, isAbort } from "./http.js";

const LIKE_CLASS = /advanced-comparison-operators/;

export function createSearch(ctx) {
  const { OGC, map } = ctx;
  const input = $("place-search");
  const list = $("place-results");
  const status = $("place-status");
  let conformance = null;
  let controller = null;
  let results = [];
  let hot = -1;

  async function supportsLike() {
    if (conformance === null) {
      conformance = getJson(`${OGC}/conformance?f=json`).then((b) => (b.conformsTo || []).some((c) => LIKE_CLASS.test(c))).catch(() => false);
    }
    return conformance;
  }

  const esc = (q) => q.replaceAll("'", "''");

  async function run(q) {
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    const collections = ctx.vectors.collections();
    if (!collections.length) { setStatus("No boundary collections are published to search."); return; }
    setStatus("Searching…");
    const lower = q.toLowerCase();
    const local = ctx.vectors.loadedFeatures()
      .filter((f) => String(f.properties?.name || "").toLowerCase().includes(lower))
      .slice(0, 8)
      .map((f) => ({ key: f.properties._key, feature: f, collection: f.properties._key.split("/")[0] }));
    const like = await supportsLike();
    const remote = await Promise.all(collections.map(async (c) => {
      const params = new URLSearchParams({ f: "json", limit: "10" });
      if (like) { params.set("filter", `name LIKE '${esc(q)}%'`); params.set("filter-lang", "cql2-text"); }
      else params.set("name", q);
      try {
        const fc = await getJson(`${OGC}/collections/${encodeURIComponent(c.id)}/items?${params}`, { signal });
        return (fc.features || []).map((f) => ({ key: `${c.id}/${f.id}`, feature: f, collection: c.id }));
      } catch (err) {
        if (isAbort(err)) throw err;
        return [{ error: `${c.title}: ${explain(err)}` }];
      }
    })).catch((err) => (isAbort(err) ? null : []));
    if (!remote || signal.aborted) return;
    const seen = new Set();
    const errors = [];
    results = [];
    for (const r of [...remote.flat(), ...local]) {
      if (r.error) { errors.push(r.error); continue; }
      if (seen.has(r.key)) continue;
      seen.add(r.key);
      results.push(r);
    }
    hot = results.length ? 0 : -1;
    render(q, like, errors);
  }

  function setStatus(text) {
    status.hidden = !text;
    status.textContent = text || "";
  }

  function render(q, like, errors) {
    list.hidden = !results.length;
    input.setAttribute("aria-expanded", results.length ? "true" : "false");
    replace(list, results.map((r, i) => {
      const p = r.feature.properties || {};
      const btn = h("button", { type: "button", role: "option", id: `place-opt-${i}`, class: i === hot ? "is-hot" : "", "aria-selected": i === hot ? "true" : "false", tabindex: "-1" },
        h("span", { text: p.name || String(r.feature.id) }),
        h("span", { class: "level", text: [p.level, r.collection].filter(Boolean).join(" · ").replaceAll("_", " ") }));
      btn.addEventListener("mousedown", (e) => e.preventDefault());
      btn.addEventListener("click", () => choose(i));
      return h("li", {}, btn);
    }));
    if (results.length) input.setAttribute("aria-activedescendant", `place-opt-${hot}`);
    else input.removeAttribute("aria-activedescendant");
    const how = like ? "prefix match" : "exact name match (the server does not offer LIKE)";
    setStatus(results.length ? (errors.length ? errors.join(" ") : "") : `No places found for “${q}” (${how}; boundaries loaded in view are also searched).${errors.length ? ` ${errors.join(" ")}` : ""}`);
  }

  function featureBbox(f) {
    const xs = []; const ys = [];
    const walk = (c) => { if (typeof c[0] === "number") { xs.push(c[0]); ys.push(c[1]); } else c.forEach(walk); };
    walk(f.geometry.coordinates);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  function choose(i) {
    const r = results[i];
    if (!r || !r.feature.geometry) return;
    const f = r.feature;
    f.properties = { ...(f.properties || {}), _key: r.key };
    ctx.vectors.rememberFeature(r.key, f);
    const b = featureBbox(f);
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 14 });
    const p = f.properties;
    ctx.selectAoi(p.name || String(f.id), f.geometry, { featureKey: r.key, source: "search", sub: `${r.collection}${p.level ? ` · ${String(p.level).replaceAll("_", " ")}` : ""} · id ${f.id}` });
    close();
    input.value = p.name || "";
    ctx.setTab("analysis");
  }

  function close() {
    list.hidden = true;
    setStatus("");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  const onInput = debounce(() => {
    const q = input.value.trim();
    if (q.length < 2) { controller?.abort(); results = []; close(); return; }
    run(q).catch(() => setStatus("Search failed."));
  }, 300);
  input.addEventListener("input", onInput);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { close(); return; }
    if (!results.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      hot = (hot + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
      [...list.querySelectorAll("button")].forEach((b, i) => { b.classList.toggle("is-hot", i === hot); b.setAttribute("aria-selected", i === hot ? "true" : "false"); });
      input.setAttribute("aria-activedescendant", `place-opt-${hot}`);
    } else if (e.key === "Enter" && hot >= 0) {
      e.preventDefault();
      choose(hot);
    }
  });
  input.addEventListener("blur", () => setTimeout(close, 150));

  return {};
}
