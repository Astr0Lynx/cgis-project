// Colour-scale logic for raster layers. Display only: nothing here changes or derives data values.
//
// Server tiles are rendered with a linear colour ramp (default viridis). OGC API - Tiles publishes the
// range it used in each tileset's `style` (title "viridis linear lo..hi"; the description says whether
// the range is a configured shared range or the dataset's own min..max). When compatible layers are
// viewed together and the published ranges differ, the viewer can request a *shared* range through the
// vendor TiTiler `rescale` parameter, derived from the server's own per-dataset statistics.

// matplotlib viridis sampled at 9 evenly spaced stops (matches TiTiler's "viridis" colormap).
export const RAMPS = {
  viridis: ["#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c", "#28ae80", "#5ec962", "#addc30", "#fde725"],
};

export function rampCss(name = "viridis") {
  const stops = RAMPS[name] || RAMPS.viridis;
  return `linear-gradient(90deg, ${stops.map((c, i) => `${c} ${((i / (stops.length - 1)) * 100).toFixed(1)}%`).join(", ")})`;
}

/** Parse an OGC tileset `style` object. Returns null when absent or unrecognised. */
export function parseStyle(style) {
  if (!style || typeof style.title !== "string") return null;
  const m = /^(\S+)\s+linear\s+(-?[\d.]+(?:e[-+]?\d+)?)\.\.(-?[\d.]+(?:e[-+]?\d+)?)$/i.exec(style.title.trim());
  if (!m) return null;
  const lo = Number(m[2]);
  const hi = Number(m[3]);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null;
  return {
    colormap: m[1],
    lo,
    hi,
    shared: /configured shared range/i.test(style.description || ""),
    source: "ogc-style",
  };
}

/** Round up to 1, 2, 2.5 or 5 × 10^k so legend labels stay readable. */
export function niceCeil(value) {
  if (!(value > 0)) return value;
  const exp = Math.floor(Math.log10(value));
  const base = 10 ** exp;
  for (const m of [1, 2, 2.5, 5, 10]) if (m * base >= value - 1e-12) return m * base;
  return 10 * base;
}

/**
 * Shared display range from TiTiler `/statistics` answers (one per dataset):
 *   lo = min(0, every band minimum); hi = nice(max of every 98th percentile).
 * The 98th percentile keeps a heavy urban tail from flattening the rest of the map; cells above `hi`
 * are drawn with the top colour and the legend says so.
 */
export function sharedRange(statsList) {
  const bands = statsList.map((s) => (s && typeof s === "object" ? Object.values(s)[0] : null)).filter(Boolean);
  if (!bands.length) return null;
  const mins = bands.map((b) => Number(b.min)).filter(Number.isFinite);
  const p98 = bands.map((b) => Number(b.percentile_98 ?? b.max)).filter(Number.isFinite);
  const maxes = bands.map((b) => Number(b.max)).filter(Number.isFinite);
  if (!p98.length) return null;
  const lo = Math.min(0, ...mins);
  let hi = niceCeil(Math.max(...p98));
  if (!(hi > lo)) hi = niceCeil(Math.max(...maxes, lo + 1));
  return { lo, hi, clipped: Math.max(...maxes) > hi, colormap: "viridis", shared: true, source: "shared-stats" };
}

export function ticks(lo, hi) {
  return [lo, (lo + hi) / 2, hi];
}

/** Are these layers allowed to share one colour scale? Same declared units only. */
export function scaleCompatible(datasets) {
  const units = new Set(datasets.map((d) => d && d.units));
  return units.size === 1 && !units.has(null) && !units.has(undefined);
}

/** True when two OGC styles portray values identically. */
export function sameStyle(a, b) {
  return Boolean(a && b && a.colormap === b.colormap && a.lo === b.lo && a.hi === b.hi);
}
