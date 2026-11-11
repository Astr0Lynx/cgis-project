// Small DOM helpers. Every piece of server-provided text goes through textContent (never innerHTML).

export const $ = (id) => document.getElementById(id);

const SVG_NS = "http://www.w3.org/2000/svg";

/** h("div", {class: "x", onclick: fn, dataset: {a: 1}, "aria-label": "…"}, child, "text", …) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function icon(name, cls = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (cls) svg.setAttribute("class", cls);
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) el.setAttribute(k, String(v));
  return el;
}

export function replace(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

const numberFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Format a number for display. Integers keep thousands separators; small values keep precision. */
export function fmt(value, { digits } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (typeof value !== "number") return String(value);
  if (digits !== undefined) {
    return value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 1000) return numberFmt.format(value);
  if (abs >= 100) return value.toLocaleString("en-IN", { maximumFractionDigits: 1 });
  if (abs >= 1) return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  return value.toLocaleString("en-IN", { maximumSignificantDigits: 3 });
}

export function fmtSigned(value, opts) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return (value > 0 ? "+" : value < 0 ? "−" : "±") + fmt(Math.abs(value), opts);
}

export function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "size unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toLocaleString("en-IN", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}

export function humanUnits(units) {
  if (!units) return "units not declared";
  const known = {
    people_per_cell: "people per cell",
    m2_per_cell: "m² per cell",
    people_per_km2: "people per km²",
  };
  return known[units] || units.replaceAll("_", " ");
}

export function toast(message, { kind = "info", timeout = 6000 } = {}) {
  const host = $("toasts");
  if (!host) return;
  const close = h("button", { class: "icon-btn", type: "button", "aria-label": "Dismiss" }, icon("x"));
  const el = h("div", { class: `toast${kind === "error" ? " is-error" : ""}`, role: kind === "error" ? "alert" : "status" },
    h("span", {}, message), close);
  close.addEventListener("click", () => el.remove());
  host.append(el);
  while (host.children.length > 3) host.firstElementChild.remove();
  if (timeout) setTimeout(() => el.remove(), timeout);
}

export function debounce(fn, ms) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function stateCard({ title, text, kind = "", action }) {
  return h("div", { class: `state-card${kind ? ` is-${kind}` : ""}` },
    h("h4", { text: title }),
    text ? h("p", { text }) : null,
    action || null);
}

export function skeleton(lines = 3) {
  return h("div", { class: "skeleton", "aria-hidden": "true" }, Array.from({ length: lines }, () => h("span")));
}
