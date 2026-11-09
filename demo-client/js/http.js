// Fetch wrapper. Normalises the project's error contract {"error": {"code", "message", "details"}}.
// Same-origin only: the viewer never sends credentials or requests to another host.

let csrfToken = null;
export function setCsrfToken(token) { csrfToken = token || null; }
export const getCsrfToken = () => csrfToken;

export class ApiError extends Error {
  constructor({ status, code, message, details, url, body }) {
    super(message || code || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code || (status ? `HTTP_${status}` : "NETWORK_ERROR");
    this.details = details || null;
    this.url = url;
    this.body = body;
  }
}

export function isAbort(err) {
  return err && (err.name === "AbortError" || err.code === "ABORTED");
}

/** Reduce a URL to a same-origin path, or null if it points elsewhere. */
export function sameOriginPath(href, origin = globalThis.location?.origin) {
  try {
    const u = new URL(href, origin);
    if (u.origin !== origin) return null;
    return u.pathname + u.search;
  } catch {
    return null;
  }
}

/**
 * request(url, {method, json, form, headers, signal}) -> {status, ok, body, headers}
 * Never throws for HTTP status codes; throws ApiError(status 0) for network failures and rethrows aborts.
 */
export async function request(url, { method = "GET", json, form, headers = {}, signal, raw = false } = {}) {
  const init = { method, signal, credentials: "same-origin", headers: { Accept: "application/json", ...headers } };
  if (json !== undefined) {
    init.body = JSON.stringify(json);
    init.headers["Content-Type"] = "application/json";
  } else if (form) {
    init.body = form;
  }
  if (method !== "GET" && method !== "HEAD" && csrfToken) init.headers["X-CSRF-Token"] = csrfToken;
  let resp;
  try {
    resp = await fetch(url, init);
  } catch (err) {
    if (isAbort(err)) throw err;
    throw new ApiError({ status: 0, code: "NETWORK_ERROR", message: "The server could not be reached.", url });
  }
  if (raw) return { status: resp.status, ok: resp.ok, headers: resp.headers, response: resp };
  const text = await resp.text();
  let body = {};
  if (text) {
    try { body = JSON.parse(text); } catch { body = { text }; }
  }
  if (resp.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("cgis:unauthorized", { detail: { url, body } }));
  }
  return { status: resp.status, ok: resp.ok, body, headers: resp.headers };
}

export function toError(res, url) {
  const e = (res.body && res.body.error) || {};
  return new ApiError({
    status: res.status, code: e.code, message: e.message || (res.body && res.body.description), details: e.details, url, body: res.body,
  });
}

export async function getJson(url, opts = {}) {
  const res = await request(url, opts);
  if (!res.ok) throw toError(res, url);
  return res.body;
}

export async function sendJson(url, payload, opts = {}) {
  const res = await request(url, { method: "POST", json: payload, ...opts });
  if (!res.ok) throw toError(res, url);
  return res.body;
}

// Human explanations for the processing / application API error codes (spatial-processing/docs/API.md).
const MESSAGES = {
  NETWORK_ERROR: "The server could not be reached. Check that the stack is running.",
  AOI_OUTSIDE_RASTER: "The area does not overlap this dataset's extent.",
  NO_PIXELS_SELECTED: "No pixel centre falls inside this area. Draw a slightly larger area.",
  AOI_TOO_LARGE: "This area is too large for an interactive operation (server pixel limit). Choose a smaller area such as a district or town.",
  AOI_TOO_COMPLEX: "The area has too many vertices or parts for the server. Simplify it or pick a boundary.",
  OUTPUT_TOO_LARGE: "The requested output exceeds the server's size limit. Clip to a smaller area.",
  REQUEST_TOO_LARGE: "The request is too large for the server (geometry too detailed).",
  INVALID_GEOMETRY: "The area geometry is invalid (for example self-intersecting).",
  EMPTY_GEOMETRY: "The area is empty.",
  UNSUPPORTED_CRS: "That coordinate reference system is not supported by the server.",
  UNSUPPORTED_FORMAT: "That output format is not supported for this dataset.",
  UNIT_SEMANTICS: "A sum is not meaningful for density units, so the server refused it.",
  DATASET_NOT_FOUND: "The dataset is no longer published.",
  DATASET_NOT_READY: "The dataset exists but is not ready yet.",
  BUSY: "The processing service is busy. Try again in a few seconds.",
  JOB_TIMEOUT: "The operation took longer than the server's time limit.",
  OUTPUT_EXPIRED: "The output has expired on the server. Run the operation again.",
  UNAUTHENTICATED: "Please sign in to continue.",
  SESSION_EXPIRED: "Your session has expired. Please sign in again.",
  INVALID_CREDENTIALS: "Wrong username or password.",
  RATE_LIMITED: "Too many attempts. Wait a moment and try again.",
  FORBIDDEN: "Your account does not have permission for this action.",
  VERSION_EXISTS: "That dataset version already exists. Use a higher version number.",
  CHECKSUM_CONFLICT: "A different file with this id and version was already uploaded.",
  FILE_TOO_LARGE: "The file is larger than the server accepts.",
  UNSUPPORTED_MEDIA_TYPE: "Only GeoTIFF/COG rasters and GeoJSON vectors can be uploaded.",
  INVALID_METADATA: "The metadata was rejected by the server.",
  INVALID_STATE: "This version is not in a state that allows that action.",
};

export function explain(err) {
  if (!err) return "Unknown error.";
  if (err.status === 401 && !MESSAGES[err.code]) return MESSAGES.UNAUTHENTICATED;
  if (err.status === 403 && !MESSAGES[err.code]) return MESSAGES.FORBIDDEN;
  return MESSAGES[err.code] || err.message || "The request failed.";
}
