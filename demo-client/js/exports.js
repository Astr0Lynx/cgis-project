// Download panel: full or clipped export with dataset/version, AOI, output CRS and format.
//
// Primary path: Agent 5's authenticated /api/exports job API (pinned provisional contract).
// Fallback when that API is not deployed: the existing Phase 2 clip operation (/api/operations/clip,
// GTiff/COG, optional output_crs). Full-dataset export has no Phase 2 equivalent and is then disabled
// with an explanation. The user never types a path or URL; CRS is chosen from a list or a validated
// EPSG code.

import { $, h, icon, replace, fmtBytes, toast } from "./dom.js";
import { request, toError, explain, sameOriginPath } from "./http.js";

export const FALLBACK_FORMATS = {
  raster: [
    { id: "GTiff", label: "GeoTIFF", extension: "tif" },
    { id: "COG", label: "Cloud-optimised GeoTIFF", extension: "tif" },
  ],
  vector: [{ id: "GeoJSON", label: "GeoJSON", extension: "geojson" }],
};

export const SUGGESTED_CRS = [
  { id: "EPSG:4326", label: "WGS 84 (lon/lat)" },
  { id: "EPSG:3857", label: "Web Mercator" },
  { id: "EPSG:7755", label: "WGS 84 / India NSF LCC" },
  { id: "EPSG:32643", label: "UTM zone 43N" },
  { id: "EPSG:32644", label: "UTM zone 44N" },
  { id: "EPSG:32645", label: "UTM zone 45N" },
];

const EPSG_RE = /^EPSG:\d{4,6}$/;

/** Validate the export form. Pure; returns {ok, errors: {field: message}, payload}. */
export function buildExportRequest({ dataset, version, kind, aoi, crsChoice, customCrs, format, formats }) {
  const errors = {};
  if (!dataset) errors.dataset = "Choose a dataset.";
  const allowed = (dataset && (formats[dataset.kind] || [])) || [];
  if (!allowed.some((f) => f.id === format)) errors.format = "Choose a format offered for this kind of dataset.";
  if (kind !== "full" && kind !== "clip") errors.kind = "Choose full dataset or clip.";
  if (kind === "clip" && !aoi) errors.kind = "Clipping needs an area of interest (Analysis tab).";
  let outputCrs = null;
  if (crsChoice === "custom") {
    const v = String(customCrs || "").trim().toUpperCase();
    if (!EPSG_RE.test(v)) errors.crs = "Enter an EPSG code such as EPSG:32644.";
    else outputCrs = v;
  } else if (crsChoice && crsChoice !== "native") {
    if (!EPSG_RE.test(crsChoice)) errors.crs = "Unsupported CRS choice.";
    else outputCrs = crsChoice;
  }
  const ok = Object.keys(errors).length === 0;
  const payload = ok ? {
    dataset_id: dataset.id,
    ...(version ? { version: Number(version) } : {}),
    kind,
    ...(kind === "clip" ? { aoi: aoi.geometry, aoi_crs: "EPSG:4326" } : {}),
    ...(outputCrs ? { output_crs: outputCrs } : {}),
    format,
  } : null;
  return { ok, errors, payload };
}

export function createExports({ config, store, auth, getCatalog, out }) {
  const app = config.applicationApi || {};
  const base = app.exports || "/api/exports";
  const useApi = Boolean(app.enabled);
  let capabilities = null;
  let job = null;            // {id, controller, timer}
  let lastResult = null;

  async function loadCapabilities() {
    if (!useApi) return null;
    try {
      const res = await request(`${base}/capabilities`);
      if (res.ok) capabilities = res.body;
    } catch { /* fall back to the contracted minimum */ }
    return capabilities;
  }

  const formats = () => capabilities?.formats || FALLBACK_FORMATS;
  const crsList = () => capabilities?.output_crs?.suggested?.length ? capabilities.output_crs.suggested : SUGGESTED_CRS;
  const acceptsAny = () => capabilities?.output_crs?.accepts_any_epsg ?? true;

  function exportable() {
    const cat = getCatalog();
    if (!cat) return [];
    return useApi ? cat.datasets.filter((d) => d.kind === "raster" || d.kind === "vector") : cat.rasters;
  }

  function render() {
    const host = $("export-panel");
    if (!host) return;
    const cat = getCatalog();
    const s = store.get();
    if (!cat) { replace(host, h("p", { class: "hint", text: "Loading datasets…" })); return; }
    const list = exportable();
    if (!list.length) {
      replace(host, h("div", { class: "state-card" }, h("h4", { text: "Nothing to export" }), h("p", { text: "No datasets are published yet." })));
      return;
    }
    if (useApi && !auth.isSignedIn()) {
      replace(host, h("div", { class: "state-card" },
        h("h4", {}, "Sign in to export"),
        h("p", { text: "Exports run as server jobs tied to your account. Users and administrators can both export." }),
        h("button", { class: "btn btn-primary", type: "button", id: "export-signin",
          onclick: () => auth.openLogin("Sign in to export datasets.") }, icon("lock"), "Sign in")));
      return;
    }

    const current = host.querySelector("#exp-dataset")?.value || s.a || list[0].id;
    const ds = cat.byId.get(current) || list[0];
    const kindSel = host.querySelector("input[name=exp-kind]:checked")?.value || (s.aoi ? "clip" : "full");
    const fmts = formats()[ds.kind] || [];
    const prevFormat = host.querySelector("#exp-format")?.value;
    const crsPrev = host.querySelector("#exp-crs")?.value || "native";

    const datasetSel = h("select", { class: "input", id: "exp-dataset" },
      list.map((d) => h("option", { value: d.id, selected: d.id === ds.id ? true : null, text: `${d.title}${d.version ? ` · v${d.version}` : ""}` })));
    datasetSel.addEventListener("change", render);

    const versions = Array.isArray(ds.versions) ? ds.versions.filter((v) => v.status === "ready") : [];
    const versionSel = h("select", { class: "input", id: "exp-version", disabled: !useApi || versions.length < 2 ? true : null },
      h("option", { value: "", text: `Current${ds.version ? ` (v${ds.version})` : ""}` }),
      versions.map((v) => h("option", { value: String(v.version), text: `v${v.version}` })));

    const fullDisabled = !useApi;
    const clipDisabled = !s.aoi || ds.kind !== "raster" && !useApi;
    const kind = fullDisabled ? "clip" : clipDisabled ? "full" : kindSel;
    const kindField = h("fieldset", { class: "segmented", id: "exp-kind" },
      h("legend", { text: "Extent" }),
      h("label", {}, h("input", { type: "radio", name: "exp-kind", value: "full", checked: kind === "full" ? true : null, disabled: fullDisabled ? true : null }), h("span", { text: "Full dataset" })),
      h("label", {}, h("input", { type: "radio", name: "exp-kind", value: "clip", checked: kind === "clip" ? true : null, disabled: clipDisabled ? true : null }), h("span", { text: "Clip to area" })));
    kindField.addEventListener("change", render);

    const crsSel = h("select", { class: "input", id: "exp-crs" },
      h("option", { value: "native", text: `Native${ds.crs ? ` (${ds.crs})` : ""}` }),
      crsList().map((c) => h("option", { value: c.id, text: `${c.id} · ${c.label}` })),
      acceptsAny() ? h("option", { value: "custom", text: "Other EPSG code…" }) : null);
    crsSel.value = [...crsSel.options].some((o) => o.value === crsPrev) ? crsPrev : "native";
    const custom = h("input", { class: "input", id: "exp-crs-custom", placeholder: "EPSG:32644", inputmode: "text",
      autocomplete: "off", spellcheck: "false", hidden: crsSel.value !== "custom" ? true : null, "aria-label": "Custom EPSG code" });
    crsSel.addEventListener("change", () => { custom.hidden = crsSel.value !== "custom"; reprojectNote(); });

    const formatSel = h("select", { class: "input", id: "exp-format" },
      fmts.map((f) => h("option", { value: f.id, text: f.label ? `${f.label} (${f.id})` : f.id })));
    if (prevFormat && fmts.some((f) => f.id === prevFormat)) formatSel.value = prevFormat;

    const note = h("div", { id: "exp-reproject-note" });
    function reprojectNote() {
      const v = crsSel.value;
      const reproj = v !== "native" && v !== (ds.crs || "");
      replace(note, reproj && ds.kind === "raster" && ds.units && /per_cell|count/.test(ds.units)
        ? h("p", { class: "note-warn" }, icon("alert"), h("span", { text: "Reprojecting a count raster resamples cells (nearest neighbour), so cell values no longer sum to the original totals. Keep the native CRS for analysis." }))
        : null);
    }
    reprojectNote();

    const aoiLine = s.aoi
      ? h("p", { class: "hint", text: `Area: ${s.aoi.label}` })
      : h("p", { class: "hint", text: "No area selected — choose one in the Analysis tab to enable clipping." });

    const blocked = (kind === "clip" && clipDisabled) || (kind === "full" && fullDisabled);
    const submit = h("button", { class: "btn btn-primary btn-block", type: "submit", id: "exp-submit", disabled: job || blocked ? true : null },
      icon("download"), useApi ? "Start export" : "Clip and export");
    const errBox = h("div", { id: "exp-errors", role: "alert" });
    const form = h("form", { id: "export-form", novalidate: true },
      field("Dataset", datasetSel, "exp-dataset"),
      field("Version", versionSel, "exp-version", !useApi ? "Only the current version is served without the application API." : versions.length < 2 ? "One published version." : null),
      h("div", { class: "field" }, h("span", { class: "field-label", text: "Extent" }), kindField, aoiLine),
      field("Output CRS", h("div", {}, crsSel, h("div", { style: { marginTop: "6px" } }, custom)), "exp-crs"),
      note,
      field("Format", formatSel, "exp-format", ds.kind === "raster" ? "Rasters: GeoTIFF or COG (values and NoData copied unchanged)." : "Vectors: GeoJSON."),
      errBox,
      submit,
      !useApi ? h("p", { class: "hint", text: "Full-dataset exports need the application API, which is not enabled on this deployment. Clips use the existing processing service (/api/operations/clip)." }) : null);
    form.addEventListener("submit", (e) => { e.preventDefault(); start(form, ds); });

    replace(host, form, h("div", { id: "export-job", "aria-live": "polite" }));
    renderJob();
  }

  function field(label, control, id, hint) {
    return h("div", { class: "field" },
      h("label", { for: id, text: label }),
      control,
      hint ? h("p", { class: "hint", style: { marginTop: "2px" }, text: hint }) : null);
  }

  async function start(form, ds) {
    const s = store.get();
    const result = buildExportRequest({
      dataset: ds,
      version: form.querySelector("#exp-version").value || null,
      kind: form.querySelector("input[name=exp-kind]:checked")?.value,
      aoi: s.aoi,
      crsChoice: form.querySelector("#exp-crs").value,
      customCrs: form.querySelector("#exp-crs-custom").value,
      format: form.querySelector("#exp-format").value,
      formats: formats(),
    });
    const errBox = form.querySelector("#exp-errors");
    form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
    if (!result.ok) {
      replace(errBox, Object.values(result.errors).map((m) => h("p", { class: "note-error", text: m })));
      if (result.errors.crs) form.querySelector("#exp-crs-custom").setAttribute("aria-invalid", "true");
      return;
    }
    replace(errBox);
    lastResult = null;
    if (useApi) await startApiJob(result.payload, ds);
    else await startClipFallback(result.payload, ds);
  }

  async function startApiJob(payload, ds) {
    job = { id: null, status: "queued", progress: null, ds, payload, controller: new AbortController() };
    render();
    try {
      const res = await request(base, { method: "POST", json: payload, signal: job.controller.signal });
      out({ request: payload, http_status: res.status, response: res.body });
      if (!res.ok) throw toError(res, base);
      job.id = res.body.job_id;
      applyJob(res.body);
      poll();
    } catch (err) {
      fail(err);
    }
  }

  function applyJob(body) {
    Object.assign(job, { status: body.status, progress: body.progress ?? null, body });
    if (body.status === "completed") {
      lastResult = { ds: job.ds, body, download: body.links?.download || body.result?.download_url };
      job = null;
      toast("Export ready to download.");
    } else if (body.status === "failed" || body.status === "cancelled") {
      lastResult = { ds: job.ds, body, error: body.error };
      job = null;
    }
    renderJob();
    const submit = $("exp-submit");
    if (submit) submit.disabled = Boolean(job);
  }

  function poll() {
    if (!job || !job.id) return;
    job.timer = setTimeout(async () => {
      if (!job) return;
      try {
        const url = `${base}/${encodeURIComponent(job.id)}`;
        const res = await request(url, { signal: job.controller.signal });
        if (!res.ok) throw toError(res, url);
        applyJob(res.body);
        if (job) poll();
      } catch (err) {
        fail(err);
      }
    }, 800);
  }

  async function cancel() {
    if (!job) return;
    const cancelHref = job.body?.links?.cancel && sameOriginPath(job.body.links.cancel);
    clearTimeout(job.timer);
    if (cancelHref) {
      try {
        const res = await request(cancelHref, { method: "POST" });
        if (res.ok) { applyJob(res.body); return; }
      } catch { /* report below */ }
    }
    job.controller.abort();
    lastResult = { ds: job.ds, body: { status: "cancelled" }, error: { code: "CANCELLED", message: cancelHref ? "Cancellation requested." : "Stopped watching this job. The server does not offer cancellation, so it may still finish." } };
    job = null;
    renderJob();
    render();
  }

  async function startClipFallback(payload, ds) {
    const clipPayload = { dataset_id: payload.dataset_id, aoi: payload.aoi, aoi_crs: payload.aoi_crs, format: payload.format };
    if (payload.output_crs) clipPayload.output_crs = payload.output_crs;
    job = { id: null, status: "running", progress: null, ds, payload: clipPayload, controller: new AbortController() };
    render();
    try {
      const res = await request("/api/operations/clip", { method: "POST", json: clipPayload, signal: job.controller.signal });
      out({ request: { ...clipPayload, aoi: store.get().aoi?.label }, http_status: res.status, response: res.body });
      if (!res.ok) throw toError(res, "/api/operations/clip");
      const body = res.body;
      const r = body.result || {};
      lastResult = {
        ds,
        download: body.links?.download || r.download_url,
        body: {
          status: body.status,
          result: {
            filename: r.filename, size_bytes: r.output?.size_bytes, media_type: r.media_type,
            output: r.output, warnings: r.warnings,
            provenance: { dataset_id: body.input?.dataset_id, version: body.input?.dataset_version, title: ds.title, source: ds.source },
          },
        },
      };
      job = null;
      renderJob();
      render();
    } catch (err) {
      fail(err);
    }
  }

  function fail(err) {
    if (err && err.name === "AbortError") return;
    lastResult = { ds: job?.ds, error: { code: err.code, message: explain(err) } };
    if (job) clearTimeout(job.timer);
    job = null;
    if (err.status === 401) auth.openLogin("Your session expired. Sign in again to export.");
    renderJob();
    render();
  }

  function renderJob() {
    const host = $("export-job");
    if (!host) return;
    if (job) {
      const pct = typeof job.progress === "number" ? Math.round(job.progress * 100) : null;
      replace(host, h("div", { class: "result", style: { marginTop: "12px" }, "data-testid": "export-job" },
        h("div", { class: "result-head" }, h("span", { class: `status status-${job.status}`, text: job.status }),
          h("span", { class: "grow", text: job.ds.title }),
          h("button", { class: "btn btn-secondary btn-sm", type: "button", id: "exp-cancel", onclick: cancel }, "Cancel")),
        h("div", { class: `progress${pct === null ? " is-indeterminate" : ""}`, role: "progressbar",
          "aria-label": "Export progress", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": pct ?? undefined },
        h("span", { style: { width: `${pct ?? 30}%` } })),
        h("p", { class: "hint", text: pct === null ? "Working…" : `${pct}% complete` })));
      return;
    }
    if (!lastResult) { replace(host); return; }
    if (lastResult.error) {
      replace(host, h("div", { class: "result", style: { marginTop: "12px" }, "data-testid": "export-result" },
        h("div", { class: "result-head" }, h("span", { class: `status status-${lastResult.body?.status === "cancelled" ? "cancelled" : "failed"}`, text: lastResult.body?.status === "cancelled" ? "cancelled" : "failed" })),
        h("p", { class: "note-error" }, h("span", { text: lastResult.error.message || "Export failed." }), " ", lastResult.error.code ? h("code", { text: lastResult.error.code }) : null)));
      return;
    }
    const r = lastResult.body?.result || {};
    const prov = r.provenance || {};
    const href = lastResult.download && sameOriginPath(lastResult.download);
    replace(host, h("div", { class: "result", style: { marginTop: "12px" }, "data-testid": "export-result" },
      h("div", { class: "result-head" }, h("span", { class: "status status-completed", text: "ready" }), h("span", { class: "grow", text: r.filename || "" })),
      h("dl", { class: "meta" },
        h("dt", { text: "File size" }), h("dd", { text: fmtBytes(r.size_bytes) }),
        h("dt", { text: "CRS" }), h("dd", { text: r.output?.crs || "—" }),
        r.output?.width ? [h("dt", { text: "Grid" }), h("dd", { text: `${r.output.width} × ${r.output.height} cells${r.output.resampled ? " (resampled)" : ""}` })] : null,
        h("dt", { text: "Dataset" }), h("dd", { text: `${prov.dataset_id || lastResult.ds?.id || ""}${prov.version ? ` v${prov.version}` : ""}` }),
        prov.source ? [h("dt", { text: "Source" }), h("dd", { text: prov.source })] : null,
        prov.license ? [h("dt", { text: "License" }), h("dd", { text: prov.license })] : null,
        prov.checksum_sha256 ? [h("dt", { text: "Source SHA-256" }), h("dd", { class: "mono", text: prov.checksum_sha256 })] : null),
      (r.warnings || r.output?.warnings || []).map((w) => h("p", { class: "note-warn" }, icon("alert"), h("span", { text: String(w) }))),
      href ? h("a", { class: "btn btn-primary btn-block", style: { marginTop: "10px" }, href, download: "", "data-testid": "export-download" }, icon("download"), "Download file") : null));
  }

  store.subscribe((s, changed) => {
    if (changed.some((k) => ["aoi", "a", "session"].includes(k))) render();
  });

  return { render, loadCapabilities };
}
