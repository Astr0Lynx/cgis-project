# Change request: Agent 6 → Agent 5 (application API consumed by the viewer)

**Status:** open. **From:** Agent 6 (`completion/agent6-webapp`). **To:** Agent 5 (`application-api/`); gateway
items are copied to Agent 4 in `docs/change-requests/agent6-to-agent4.md`.
**Timing:** needed before checkpoint C5 (branch freeze) so Agent 7 can run the browser suite against the
integrated stack. Agent 6 has not edited any Agent 5 path.

At the frozen base `c4dac59` no Agent 5 OpenAPI was published, so the viewer pinned a **provisional
consumer contract** in [`docs/openapi.agent5.provisional.json`](docs/openapi.agent5.provisional.json) and
isolated every call in `js/auth.js`, `js/admin.js` and `js/exports.js`. **Your published OpenAPI wins.**
Please confirm each item below, or send the difference; the viewer adapts in those three files only.

## 1. Authentication (`/auth`)

| Need | Pinned shape | Why |
|---|---|---|
| `POST /auth/login` `{username, password}` | 200 `Session`; 401 `INVALID_CREDENTIALS`; 429 `RATE_LIMITED` + `Retry-After` | login dialog, error text |
| `POST /auth/logout` | 204, idempotent | |
| `GET /auth/me` | **200 for anonymous** with `{"authenticated": false, "user": null}` | A 401 on page load makes Chromium log a console error, and the viewer's acceptance check fails on any console error during a nominal session |
| `Session` | `{authenticated, user: {username, role: "admin"\|"user"}, expires_at, csrf_token?}` | role-aware screens; expiry timer |
| Cookie session | HttpOnly, SameSite=Strict, Path=/ | browser |
| CSRF | if a token is issued, the viewer echoes it as `X-CSRF-Token` on every POST (fetch and the XHR upload) | |
| Expiry | 401 with code `SESSION_EXPIRED` (or any 401) on protected routes | the viewer shows a banner and re-login, then retries |

## 2. Admin datasets (`/admin`)

| Route | Pinned shape |
|---|---|
| `GET /admin/datasets?q=&status=&offset=&limit=` | `{items: DatasetSummary[], total, offset, limit}` |
| `GET /admin/datasets/{dataset_id}` | `DatasetSummary` + `versions: VersionRecord[]` (status, checksum, size, CRS, created_by, provenance, validation `{ok, errors[{code,message}], warnings}`) |
| `POST /admin/datasets/uploads` (multipart `file` + `metadata` JSON text) | 202 `AdminJob`; 409 `VERSION_EXISTS` / `CHECKSUM_CONFLICT`; 413 `FILE_TOO_LARGE`; 415; 422 `INVALID_METADATA` |
| `POST /admin/datasets/{id}/versions/{v}/validate` | 202 `AdminJob`; 409 `INVALID_STATE` |
| `POST /admin/datasets/{id}/versions/{v}/publish` | 202 `AdminJob`; 409 `INVALID_STATE` |
| `GET /admin/jobs/{job_id}` | `AdminJob {job_id, kind, status queued\|running\|completed\|failed, progress?, dataset_id, version, result, error}` |

Version status values used by the UI: `staged, validating, validated, publishing, ready, failed`.
User-role calls must return **403** and anonymous calls **401**. The browser suite tests direct HTTP
bypass with a user session (`tests/e2e/auth-admin.spec.js`).

**Upload size:** the gateway's `client_max_body_size 1m` on `/api/` would block real rasters. Please agree
with Agent 4 on a dedicated upload route/limit (or chunked/resumable upload). The viewer shows upload
progress through `XMLHttpRequest.upload.onprogress` and needs nothing else.

## 3. Exports (`/api/exports`)

| Route | Pinned shape |
|---|---|
| `GET /api/exports/capabilities` | `{kinds: ["full","clip"], formats: {raster: [{id,label,media_type,extension}], vector: [...]}, output_crs: {suggested: [{id,label}], accepts_any_epsg}}` — **requested addition** so the UI never offers a format/CRS the server refuses |
| `POST /api/exports` | `{dataset_id, version?, kind: full\|clip, aoi?, aoi_crs?, output_crs?, format}` → 202 `ExportJob` |
| `GET /api/exports/{job_id}` | `ExportJob {status queued\|running\|completed\|failed\|cancelled, progress?, result {filename, media_type, size_bytes, output{crs,width,height,resampled}, provenance{dataset_id, version, title, source, license, checksum_sha256}, download_url}, error, links{self, download?, cancel?}}` |
| `POST /api/exports/{job_id}/cancel` | only when `links.cancel` is present; the UI otherwise "stops watching" and says so |
| `GET /api/exports/{job_id}/download` | attachment |

Without `capabilities` the viewer offers only the contract minimum: rasters GTiff/COG, vectors GeoJSON.

## 4. Public catalog metadata (optional, recommended)

The OGC collections do not expose `provenance`, so the viewer cannot show **license**, **logical Level 2
group** (`provenance.group_id`) or a verified **synthetic/real** badge for real data. It currently shows
"not exposed" and groups Level 2 by year. Proposed read-only, unauthenticated, path-free endpoint:

`GET /api/catalog/datasets` → `{items: [{dataset_id, version, title, kind, product_level, year, units, crs, nodata,
resolution_x, resolution_y, source, license, citation, synthetic, group_id, region, versions: [{version, status}]}]}`

The viewer consumes it when `config.json → catalogMetadata` is set to its URL. Agent 4 could instead add the
same fields to the OGC collection JSON. Either one closes the gap.

## Tests the viewer runs against this contract

`tests/mock-gateway/server.mjs` implements the pinned shapes, and `tests/e2e/auth-admin.spec.js` and
`tests/e2e/exports.spec.js` cover login errors, roles, admin upload → validate → publish → map,
duplicate/corrupt uploads, user 403 on direct HTTP, anonymous 401, CSRF refusal, session expiry,
full/clip/CRS/format exports with GeoTIFF georeferencing checks, and cancel.
