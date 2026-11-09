// Minimal observable store plus URL-hash persistence so a map state is reproducible from a link.

export function createStore(initial) {
  const state = { ...initial };
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      const changed = Object.keys(patch).filter((k) => !Object.is(state[k], patch[k]));
      if (!changed.length) return;
      Object.assign(state, patch);
      for (const fn of listeners) fn(state, changed);
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

// #/map?a=<id>&b=<id>&mode=swipe&src=vendor&scale=shared&c=lon,lat,zoom
const HASH_KEYS = ["a", "b", "mode", "src", "scale", "c"];

export function parseHash(hash = globalThis.location?.hash || "") {
  const raw = hash.replace(/^#/, "");
  const [path, query = ""] = raw.split("?");
  const view = path.replace(/^\//, "") || "map";
  const params = new URLSearchParams(query);
  const out = { view: view === "admin" ? "admin" : "map" };
  for (const key of HASH_KEYS) if (params.has(key)) out[key] = params.get(key);
  if (out.c) {
    const [lng, lat, zoom] = out.c.split(",").map(Number);
    out.camera = [lng, lat, zoom].every(Number.isFinite) ? { center: [lng, lat], zoom } : null;
  }
  return out;
}

export function formatHash(view, params) {
  const q = new URLSearchParams();
  for (const key of HASH_KEYS) {
    const v = params[key];
    if (v !== undefined && v !== null && v !== "") q.set(key, v);
  }
  const qs = q.toString();
  return `#/${view}${qs ? `?${qs}` : ""}`;
}
