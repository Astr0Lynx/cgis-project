// Viewport-scoped, paginated OGC API - Features loading.
//
// Never one `limit=1000` request for a whole country: every request carries the current bbox, pages
// are followed through the server's rel=next links (same-origin only), and a per-view feature budget
// stops runaway downloads. When the server reports more matches than the budget, nothing partial is
// drawn; the UI says "zoom in" instead of silently truncating.

import { sameOriginPath } from "./http.js";

export const DEFAULTS = { pageSize: 500, maxFeaturesPerView: 5000, maxPages: 40 };

export function clampBbox(view, extent) {
  const v = [Math.max(-180, view[0]), Math.max(-90, view[1]), Math.min(180, view[2]), Math.min(90, view[3])];
  if (!extent) return v[0] < v[2] && v[1] < v[3] ? v : null;
  const out = [Math.max(v[0], extent[0]), Math.max(v[1], extent[1]), Math.min(v[2], extent[2]), Math.min(v[3], extent[3])];
  return out[0] < out[2] && out[1] < out[3] ? out : null;
}

const round = (n) => Number(n.toFixed(6));

export function itemsUrl(base, collectionId, bbox, limit) {
  const q = new URLSearchParams({ f: "json", bbox: bbox.map(round).join(","), limit: String(limit) });
  return `${base}/collections/${encodeURIComponent(collectionId)}/items?${q}`;
}

function nextHref(fc, origin) {
  const link = (fc.links || []).find((l) => l.rel === "next" && l.href);
  return link ? sameOriginPath(link.href, origin) : null;
}

/**
 * Load every feature of `collectionId` intersecting `bbox`, page by page.
 * fetchJson(url, {signal}) must reject on HTTP errors. Resolves to
 *   {status: "ok"|"empty"|"too_many"|"budget", features, numberMatched, pages, requests: [urls]}
 * and rejects with AbortError when `signal` aborts.
 */
export async function loadInBbox({ base, collectionId, bbox, fetchJson, signal, origin, options = {} }) {
  const opts = { ...DEFAULTS, ...options };
  const requests = [];
  if (!bbox) return { status: "empty", features: [], numberMatched: 0, pages: 0, requests };
  let url = itemsUrl(base, collectionId, bbox, opts.pageSize);
  const features = [];
  const seen = new Set();
  let numberMatched = null;
  let pages = 0;
  while (url && pages < opts.maxPages) {
    requests.push(url);
    const fc = await fetchJson(url, { signal });
    pages += 1;
    if (typeof fc.numberMatched === "number") numberMatched = fc.numberMatched;
    if (pages === 1 && numberMatched !== null && numberMatched > opts.maxFeaturesPerView) {
      return { status: "too_many", features: [], numberMatched, pages, requests };
    }
    for (const f of fc.features || []) {
      const key = f.id ?? JSON.stringify(f.properties);
      if (seen.has(key)) continue;
      seen.add(key);
      features.push(f);
    }
    if (features.length >= opts.maxFeaturesPerView) {
      return { status: "budget", features: [], numberMatched, pages, requests };
    }
    const returned = (fc.features || []).length;
    if (!returned || (numberMatched !== null && features.length >= numberMatched)) break;
    url = nextHref(fc, origin);
  }
  return { status: features.length ? "ok" : "empty", features, numberMatched: numberMatched ?? features.length, pages, requests };
}

/**
 * Keeps one in-flight load per collection: a new view aborts the previous one, so rapid panning never
 * leaves stale requests or paints an older view over a newer one.
 */
export class ViewportLoader {
  constructor({ base, collectionId, extent, fetchJson, origin, options, onState }) {
    Object.assign(this, { base, collectionId, extent, fetchJson, origin, options, onState });
    this.controller = null;
    this.generation = 0;
  }

  abort() {
    if (this.controller) this.controller.abort();
    this.controller = null;
  }

  async load(viewBbox) {
    this.abort();
    const generation = ++this.generation;
    const controller = new AbortController();
    this.controller = controller;
    const bbox = clampBbox(viewBbox, this.extent);
    if (!bbox) {
      this.onState?.({ status: "outside", features: [], numberMatched: 0 });
      return null;
    }
    this.onState?.({ status: "loading" });
    try {
      const result = await loadInBbox({
        base: this.base, collectionId: this.collectionId, bbox, fetchJson: this.fetchJson,
        signal: controller.signal, origin: this.origin, options: this.options,
      });
      if (generation !== this.generation) return null;
      this.onState?.(result);
      return result;
    } catch (err) {
      if (err && err.name === "AbortError") return null;
      if (generation === this.generation) this.onState?.({ status: "error", error: err, features: [] });
      return null;
    } finally {
      if (this.controller === controller) this.controller = null;
    }
  }
}
