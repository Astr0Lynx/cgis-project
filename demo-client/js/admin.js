// Administrator screens: dataset list with version status, upload -> validate -> publish, job progress.
// Visible only to role=admin, but the backend is the authority: a user who reaches #/admin sees a 403
// explanation, and every call still carries the session so the server can refuse it.

import { $, h, icon, replace, fmtBytes, toast, debounce } from "./dom.js";
import { request, toError, explain, getCsrfToken } from "./http.js";

const STATUS_FILTERS = ["", "staged", "validating", "validated", "publishing", "ready", "failed"];
const PAGE = 20;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const ACCEPT = ".tif,.tiff,.geojson,.json";

/** Client-side checks mirroring the importer's contract; the server re-validates everything. */
export function validateUpload(meta, file) {
  const errors = {};
  if (!file) errors.file = "Choose a GeoTIFF/COG or GeoJSON file.";
  else {
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    const kindByExt = { tif: "raster", tiff: "raster", geojson: "vector", json: "vector" }[ext];
    if (!kindByExt) errors.file = "Only .tif/.tiff (raster) or .geojson/.json (vector) files are accepted.";
    else if (meta.kind && kindByExt !== meta.kind) errors.kind = `A .${ext} file is a ${kindByExt} dataset.`;
  }
  if (!ID_RE.test(meta.dataset_id || "")) errors.dataset_id = "Use letters, digits, '.', '_' or '-' (max 128), starting with a letter or digit.";
  if (!(Number.isInteger(meta.version) && meta.version >= 1)) errors.version = "Version must be a whole number ≥ 1.";
  if (!meta.title || !meta.title.trim()) errors.title = "Title is required.";
  if (!["raster", "vector"].includes(meta.kind)) errors.kind = "Choose raster or vector.";
  if (!["source", "level1", "level2", "other"].includes(meta.product_level)) errors.product_level = "Choose a product level.";
  if (meta.year !== null && !(Number.isInteger(meta.year) && meta.year >= 1900 && meta.year <= 2100)) errors.year = "Year must be between 1900 and 2100, or empty.";
  if (meta.kind === "raster" && !meta.units) errors.units = "Rasters need units (e.g. people_per_cell). Counts and densities are never converted.";
  return { ok: Object.keys(errors).length === 0, errors };
}

export function createAdmin({ config, store, auth, reloadCatalog }) {
  const base = (config.applicationApi || {}).admin || "/admin";
  const root = $("view-admin");
  const view = { q: "", status: "", offset: 0, page: null, loading: false, error: null, selected: null, detail: null, jobs: new Map() };

  function guard() {
    const s = store.get().session || {};
    if (!auth.enabled || s.status === "unavailable") {
      return block("Administration is not enabled", "This deployment does not run the application API (accounts, upload, publish). Ask the operator to enable it.", null);
    }
    if (s.status !== "authenticated") {
      return block("Sign in as an administrator", "Dataset upload and publication are restricted to administrators.",
        h("button", { class: "btn btn-primary", type: "button", onclick: () => auth.openLogin("Sign in with an administrator account to manage datasets.") }, icon("lock"), "Sign in"));
    }
    if (s.user?.role !== "admin") {
      return block("Administrator role required (403)",
        `You are signed in as ${s.user?.username} with the read-only user role. You can view, analyse and export data, but uploading and publishing are refused by the server for this role.`,
        h("a", { class: "btn btn-secondary", href: "#/map" }, "Back to the map"));
    }
    return true;
  }

  function block(title, text, action) {
    replace(root, h("div", { class: "admin-inner" }, h("div", { class: "card forbidden", "data-testid": "admin-blocked" },
      h("div", { class: "card-body" }, h("h1", { style: { fontSize: "18px", margin: "0 0 6px" }, text: title }),
        h("p", { style: { color: "var(--ink-3)", margin: "0 0 14px" }, text }), action))));
    return false;
  }

  async function open() {
    if (guard() !== true) return;
    layout();
    await loadList();
  }

  let listCard, detailCard, uploadCard;
  function layout() {
    listCard = h("section", { class: "card", "aria-labelledby": "adm-list-h" });
    uploadCard = h("section", { class: "card", "aria-labelledby": "adm-upload-h" });
    detailCard = h("section", { class: "card", "aria-labelledby": "adm-detail-h", hidden: true });
    replace(root, h("div", { class: "admin-inner" },
      h("header", { class: "admin-header" },
        h("div", { class: "grow" },
          h("h1", { text: "Datasets" }),
          h("p", { text: "Upload a GeoTIFF/COG or GeoJSON into server-managed staging, validate it against the importer's checks, then publish it as a new catalog version. Older versions are kept." })),
        h("a", { class: "btn btn-secondary", href: "#/map" }, "Open map")),
      h("div", { class: "admin-grid" },
        h("div", {}, listCard, detailCard),
        h("div", {}, uploadCard))));
    renderList();
    renderUpload();
  }

  async function api(path, opts = {}) {
    const res = await request(`${base}${path}`, opts);
    if (!res.ok) {
      const err = toError(res, `${base}${path}`);
      if (res.status === 401) auth.openLogin("Your session expired. Sign in again to continue.", () => open());
      throw err;
    }
    return res.body;
  }

  async function loadList() {
    view.loading = true;
    view.error = null;
    renderList();
    const q = new URLSearchParams({ offset: String(view.offset), limit: String(PAGE) });
    if (view.q) q.set("q", view.q);
    if (view.status) q.set("status", view.status);
    try {
      view.page = await api(`/datasets?${q}`);
    } catch (err) {
      // 403 here means the server no longer grants the admin role; the error card says so.
      view.error = err;
    } finally {
      view.loading = false;
      renderList();
    }
  }

  function renderList() {
    if (!listCard) return;
    const search = h("input", { class: "input", type: "search", placeholder: "Search id or title", value: view.q, "aria-label": "Search datasets", style: { maxWidth: "260px" } });
    search.addEventListener("input", debounce(() => { view.q = search.value.trim(); view.offset = 0; loadList(); }, 300));
    const status = h("select", { class: "input", "aria-label": "Filter by status", style: { width: "160px" } },
      STATUS_FILTERS.map((s) => h("option", { value: s, selected: s === view.status ? true : null, text: s ? `Status: ${s}` : "All statuses" })));
    status.addEventListener("change", () => { view.status = status.value; view.offset = 0; loadList(); });
    const refresh = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => loadList() }, "Refresh");

    let body;
    if (view.loading && !view.page) body = h("div", { class: "card-body" }, h("div", { class: "skeleton" }, h("span"), h("span"), h("span")));
    else if (view.error) body = h("div", { class: "card-body" }, h("div", { class: "state-card is-error" }, h("h4", { text: "Could not load datasets" }), h("p", { text: explain(view.error) }), refresh.cloneNode(true)));
    else if (!view.page?.items?.length) body = h("div", { class: "card-body" }, h("div", { class: "state-card" }, h("h4", { text: view.q || view.status ? "No datasets match" : "No datasets yet" }), h("p", { text: "Upload a file with the form on the right." })));
    else {
      body = h("div", { class: "table-wrap" }, h("table", { class: "admin-table", "data-testid": "admin-table" },
        h("thead", {}, h("tr", {}, ["Dataset", "Level", "Year", "Current", "Latest", "Updated"].map((t) => h("th", { scope: "col", text: t })))),
        h("tbody", {}, view.page.items.map((d) => {
          const tr = h("tr", { class: d.dataset_id === view.selected ? "is-selected" : "" },
            h("td", {}, h("button", { class: "row-link", type: "button", text: d.title || d.dataset_id, onclick: (e) => { e.stopPropagation(); select(d.dataset_id); } }), h("span", { class: "mono", text: d.dataset_id })),
            h("td", { text: `${d.kind} · ${d.product_level}` }),
            h("td", { class: "num", text: d.year ?? "—" }),
            h("td", { class: "num", text: d.current_version ? `v${d.current_version}` : "—" }),
            h("td", {}, h("span", { class: `status status-${d.latest_status}`, text: `v${d.latest_version} ${d.latest_status}` })),
            h("td", { class: "num", text: d.updated_at ? new Date(d.updated_at).toLocaleString() : "—" }));
          tr.addEventListener("click", () => select(d.dataset_id));
          return tr;
        }))));
    }
    const total = view.page?.total ?? 0;
    const pager = view.page && total > PAGE ? h("div", { class: "pager" },
      h("span", { class: "grow", text: `${view.offset + 1}–${Math.min(view.offset + PAGE, total)} of ${total}` }),
      h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: view.offset === 0 ? true : null, onclick: () => { view.offset = Math.max(0, view.offset - PAGE); loadList(); } }, "Previous"),
      h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: view.offset + PAGE >= total ? true : null, onclick: () => { view.offset += PAGE; loadList(); } }, "Next")) : null;
    replace(listCard,
      h("div", { class: "card-head" }, h("h2", { id: "adm-list-h", text: "Catalog" }), view.page ? h("span", { class: "hint", style: { margin: 0 }, text: `${total} dataset${total === 1 ? "" : "s"}` }) : null),
      h("div", { class: "toolbar" }, search, status, h("span", { class: "grow" }), refresh),
      body, pager);
  }

  async function select(id) {
    view.selected = id;
    renderList();
    detailCard.hidden = false;
    replace(detailCard, h("div", { class: "card-head" }, h("h2", { id: "adm-detail-h", text: id })), h("div", { class: "card-body" }, h("div", { class: "skeleton" }, h("span"), h("span"))));
    try {
      view.detail = await api(`/datasets/${encodeURIComponent(id)}`);
      renderDetail();
    } catch (err) {
      replace(detailCard, h("div", { class: "card-head" }, h("h2", { id: "adm-detail-h", text: id })),
        h("div", { class: "card-body" }, h("p", { class: "note-error", text: explain(err) })));
    }
  }

  function renderDetail() {
    const d = view.detail;
    if (!d) return;
    replace(detailCard,
      h("div", { class: "card-head" }, h("h2", { id: "adm-detail-h", text: d.title || d.dataset_id }), h("span", { class: "mono hint", style: { margin: 0 }, text: d.dataset_id })),
      h("div", { class: "card-body" },
        h("ul", { class: "versions", "data-testid": "admin-versions" }, (d.versions || []).slice().sort((a, b) => b.version - a.version).map((v) => {
          const jobState = [...view.jobs.values()].find((j) => j.dataset_id === d.dataset_id && j.version === v.version && (j.status === "queued" || j.status === "running"));
          return h("li", {},
            h("div", { class: "version-head" },
              h("strong", { text: `v${v.version}` }),
              h("span", { class: `status status-${v.status}`, text: v.status }),
              d.current_version === v.version ? h("span", { class: "badge badge-real" }, icon("check"), "Current") : null,
              h("span", { class: "grow" }),
              h("span", { class: "hint", style: { margin: 0 }, text: [v.crs, v.size_bytes ? fmtBytes(v.size_bytes) : null, v.created_by].filter(Boolean).join(" · ") })),
            v.validation && v.validation.errors?.length ? h("div", {}, v.validation.errors.map((e) => h("p", { class: "note-error" }, h("code", { text: e.code }), " ", e.message))) : null,
            v.validation && v.validation.ok ? h("p", { class: "hint", text: `Validation passed${v.validation.warnings?.length ? ` with ${v.validation.warnings.length} warning(s)` : ""}.` }) : null,
            v.checksum_sha256 ? h("p", { class: "hint mono", text: `sha256 ${v.checksum_sha256}` }) : null,
            jobState ? h("div", { class: "progress is-indeterminate", role: "progressbar", "aria-label": `${jobState.kind} in progress` }, h("span")) : null,
            h("div", { class: "version-actions" },
              ["staged", "failed"].includes(v.status) ? h("button", { class: "btn btn-secondary btn-sm", type: "button", "data-action": "validate", disabled: jobState ? true : null, onclick: () => action(d.dataset_id, v.version, "validate") }, "Validate") : null,
              v.status === "validated" ? h("button", { class: "btn btn-primary btn-sm", type: "button", "data-action": "publish", disabled: jobState ? true : null, onclick: () => action(d.dataset_id, v.version, "publish") }, "Publish") : null,
              v.provenance ? h("details", {}, h("summary", { class: "hint", style: { cursor: "pointer" }, text: "Provenance" }), h("pre", { class: "json", text: JSON.stringify(v.provenance, null, 2) })) : null));
        }))));
  }

  async function action(id, version, kind) {
    try {
      const jobBody = await api(`/datasets/${encodeURIComponent(id)}/versions/${version}/${kind}`, { method: "POST" });
      track(jobBody);
      renderDetail();
    } catch (err) {
      toast(explain(err), { kind: "error" });
    }
  }

  function track(jobBody) {
    view.jobs.set(jobBody.job_id, jobBody);
    const tick = async () => {
      try {
        const j = await api(`/jobs/${encodeURIComponent(jobBody.job_id)}`);
        view.jobs.set(j.job_id, j);
        if (j.status === "queued" || j.status === "running") { setTimeout(tick, 700); renderUploadJob(); return; }
        if (j.status === "completed") {
          toast(j.kind === "publish" ? `Published ${j.dataset_id} v${j.version}. It appears on the map after the OGC publisher refreshes.` : `${j.kind} of ${j.dataset_id} v${j.version} finished.`);
          if (j.kind === "publish") reloadCatalog?.();
        } else {
          toast(`${j.kind} of ${j.dataset_id} v${j.version} failed: ${j.error?.message || j.error?.code || "see version details"}`, { kind: "error" });
        }
        renderUploadJob();
        await loadList();
        if (view.selected === j.dataset_id) await select(j.dataset_id);
      } catch (err) {
        toast(explain(err), { kind: "error" });
      }
    };
    setTimeout(tick, 400);
  }

  // ---- upload ----
  let uploadState = null; // {progress, xhr, job}
  function renderUpload() {
    const fileInput = h("input", { type: "file", id: "upl-file", accept: ACCEPT });
    const drop = h("label", { class: "drop", for: "upl-file" }, icon("upload"), h("strong", { text: "Choose a file or drop it here" }), h("span", { text: "GeoTIFF / COG raster or GeoJSON vector. The server stores it in managed staging; paths and URLs are never accepted." }), fileInput);
    const chip = h("div", { id: "upl-chip" });
    let file = null;
    const kindSel = sel("upl-kind", [["raster", "Raster"], ["vector", "Vector"]]);
    const setFile = (f) => {
      file = f || null;
      replace(chip, file ? h("div", { class: "file-chip" }, h("span", { class: "grow", text: file.name }), h("span", { class: "hint", style: { margin: 0 }, text: fmtBytes(file.size) })) : null);
      if (file) {
        const ext = (file.name.split(".").pop() || "").toLowerCase();
        if (["geojson", "json"].includes(ext)) kindSel.value = "vector";
        if (["tif", "tiff"].includes(ext)) kindSel.value = "raster";
      }
    };
    fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("is-over"); setFile(e.dataTransfer.files[0]); });

    const fields = {
      dataset_id: h("input", { class: "input", id: "upl-id", autocomplete: "off", spellcheck: "false", placeholder: "lowercase-slug-with-year" }),
      version: h("input", { class: "input", id: "upl-version", type: "number", min: "1", step: "1", value: "1" }),
      title: h("input", { class: "input", id: "upl-title" }),
      kind: kindSel,
      product_level: sel("upl-level", [["level1", "Level 1"], ["level2", "Level 2"], ["source", "Source"], ["other", "Other"]]),
      year: h("input", { class: "input", id: "upl-year", type: "number", min: "1900", max: "2100", step: "1" }),
      units: h("input", { class: "input", id: "upl-units", placeholder: "people_per_cell", autocomplete: "off" }),
      source: h("input", { class: "input", id: "upl-source", placeholder: "Upstream product or model" }),
      license: h("input", { class: "input", id: "upl-license", placeholder: "e.g. CC BY 4.0" }),
      group_id: h("input", { class: "input", id: "upl-group", placeholder: "optional logical group" }),
      description: h("textarea", { class: "input", id: "upl-desc", rows: "2" }),
      synthetic: h("input", { type: "checkbox", id: "upl-synthetic" }),
    };
    const errs = {};
    const f = (key, label, req, span) => {
      errs[key] = h("p", { class: "field-error", id: `err-${key}`, hidden: true });
      fields[key].setAttribute("aria-describedby", `err-${key}`);
      return h("div", { class: `field${span ? " span-2" : ""}` }, h("label", { for: fields[key].id }, label, req ? h("span", { class: "req", "aria-hidden": "true", text: " *" }) : null), fields[key], errs[key]);
    };
    errs.file = h("p", { class: "field-error", id: "err-file", hidden: true });
    const submit = h("button", { class: "btn btn-primary btn-block", type: "submit", id: "upl-submit" }, icon("upload"), "Upload and validate");
    const form = h("form", { id: "upload-form", novalidate: true },
      drop, chip, errs.file,
      h("div", { class: "form-grid", style: { marginTop: "12px" } },
        f("dataset_id", "Dataset id", true, true), f("version", "Version", true), f("kind", "Kind", true),
        f("title", "Title", true, true), f("product_level", "Product level", true), f("year", "Year"),
        f("units", "Units"), f("source", "Source"), f("license", "License", false, true), f("group_id", "Group id", false, true),
        f("description", "Description", false, true)),
      h("label", { class: "check", style: { margin: "2px 0 12px" } }, fields.synthetic, "This file is synthetic / test data"),
      submit, h("div", { id: "upl-job", "aria-live": "polite" }));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const yearRaw = fields.year.value.trim();
      const meta = {
        dataset_id: fields.dataset_id.value.trim(),
        version: Number(fields.version.value),
        title: fields.title.value.trim(),
        description: fields.description.value.trim() || undefined,
        kind: fields.kind.value,
        product_level: fields.product_level.value,
        year: yearRaw === "" ? null : Number(yearRaw),
        units: fields.units.value.trim() || undefined,
        source: fields.source.value.trim() || undefined,
        provenance: {
          ...(fields.license.value.trim() ? { license: fields.license.value.trim() } : {}),
          ...(fields.group_id.value.trim() ? { group_id: fields.group_id.value.trim() } : {}),
          synthetic: fields.synthetic.checked,
        },
      };
      const { ok, errors } = validateUpload(meta, file);
      for (const [key, el] of Object.entries(errs)) {
        el.hidden = !errors[key];
        el.textContent = errors[key] || "";
        if (fields[key]) fields[key].setAttribute("aria-invalid", errors[key] ? "true" : "false");
      }
      if (!ok) { form.querySelector("[aria-invalid=true]")?.focus(); return; }
      upload(file, meta, submit);
    });
    replace(uploadCard, h("div", { class: "card-head" }, h("h2", { id: "adm-upload-h", text: "Upload a new version" })), h("div", { class: "card-body" }, form));
  }

  function sel(id, options) {
    return h("select", { class: "input", id }, options.map(([v, t]) => h("option", { value: v, text: t })));
  }

  function upload(file, meta, submit) {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("metadata", JSON.stringify(meta));
    const xhr = new XMLHttpRequest();
    uploadState = { progress: 0, xhr, job: null, name: file.name };
    submit.disabled = true;
    renderUploadJob();
    xhr.open("POST", `${base}/datasets/uploads`);
    xhr.setRequestHeader("Accept", "application/json");
    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { uploadState.progress = e.loaded / e.total; renderUploadJob(); } };
    xhr.onerror = () => { uploadState.error = { message: "Upload failed: the server could not be reached." }; submit.disabled = false; renderUploadJob(); };
    xhr.onload = () => {
      submit.disabled = false;
      let body = {};
      try { body = JSON.parse(xhr.responseText || "{}"); } catch { body = {}; }
      if (xhr.status === 401) { uploadState = null; renderUploadJob(); auth.openLogin("Your session expired. Sign in again, then retry the upload."); return; }
      if (xhr.status < 200 || xhr.status >= 300) {
        uploadState.error = { code: body.error?.code, message: explain({ status: xhr.status, code: body.error?.code, message: body.error?.message }) };
        renderUploadJob();
        return;
      }
      uploadState.job = body;
      renderUploadJob();
      track(body);
      toast(`Uploaded ${file.name}; validation started.`);
    };
    xhr.send(form);
  }

  function renderUploadJob() {
    const host = $("upl-job");
    if (!host || !uploadState) { if (host) replace(host); return; }
    const job = uploadState.job ? view.jobs.get(uploadState.job.job_id) || uploadState.job : null;
    let content;
    if (uploadState.error) {
      content = h("p", { class: "note-error" }, h("span", { text: uploadState.error.message }), uploadState.error.code ? [" ", h("code", { text: uploadState.error.code })] : null);
    } else if (!job) {
      const pct = Math.round(uploadState.progress * 100);
      content = [h("div", { class: "progress", role: "progressbar", "aria-label": "Upload progress", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct) }, h("span", { style: { width: `${pct}%` } })),
        h("p", { class: "hint", text: `Uploading ${uploadState.name}: ${pct}%` })];
    } else {
      const running = job.status === "queued" || job.status === "running";
      content = h("div", { class: "result", "data-testid": "upload-job" },
        h("div", { class: "result-head" }, h("span", { class: `status status-${job.status}`, text: `${job.kind} ${job.status}` }), h("span", { class: "grow", text: `${job.dataset_id} v${job.version}` })),
        running ? h("div", { class: "progress is-indeterminate" }, h("span")) : null,
        job.error ? h("p", { class: "note-error" }, h("code", { text: job.error.code || "FAILED" }), " ", job.error.message || "") : null,
        job.status === "completed" ? h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => select(job.dataset_id) }, "Review and publish") : null);
    }
    replace(host, h("div", { style: { marginTop: "12px" } }, content));
  }

  store.subscribe((s, changed) => {
    if (changed.includes("session") && s.view === "admin") open();
  });

  return { open };
}
