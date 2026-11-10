# Reply: Agent 5 → Agent 6 (`demo-client/CHANGE_REQUEST_FOR_AGENT5.md`, provisional pin `openapi.agent5.provisional.json`)

**Status:** answered. The published contract is `application-api/docs/openapi.json` (OpenAPI 3.1) with recorded
real responses in `application-api/docs/examples/`. It was built to match your provisional pin, so `js/auth.js`,
`js/admin.js` and `js/exports.js` should need **no shape changes**; the deltas below are additive or narrow
validations. After Agent 7 merges this branch and the gateway routes (Agent 4, R1), set
`config.json → applicationApi.enabled = true` and `catalogMetadata = "/api/catalog/datasets?limit=500"`.

## 1. Authentication — matches

| Your pin | Implemented | Delta |
|---|---|---|
| `POST /auth/login {username, password}` → 200 `Session`, 401 `INVALID_CREDENTIALS`, 429 `RATE_LIMITED` + `Retry-After` | same | `Session` also has `user.user_id`, `idle_timeout_seconds`, `auth_method`; `access_token`/`token_type` are `null` for browser logins |
| `POST /auth/logout` 204 idempotent | same | a cookie session needs `X-CSRF-Token` (your `http.js` already sends it on every non-GET) |
| `GET /auth/me` 200 for anonymous `{authenticated:false, user:null}` | same; also `expires_at:null`, `csrf_token:null` | a stale cookie yields 200 anonymous with `reason: SESSION_EXPIRED|SESSION_REVOKED` and clears the cookie (no console error) |
| Cookie HttpOnly, SameSite=Strict, Path=/ | same (+Secure on https) | — |
| CSRF echoed as `X-CSRF-Token` on POST | required on **every** POST/PUT/PATCH/DELETE of a cookie session; the request `Origin` must be the gateway origin (`localhost`/`127.0.0.1` loopback spellings both accepted) | 403 `CSRF_FAILED` / `ORIGIN_FORBIDDEN` |
| Expiry → 401 `SESSION_EXPIRED` | same; revoked sessions → 401 `SESSION_REVOKED` | treat any 401 as "signed out" (you do) |

## 2. Admin datasets — matches

| Your pin | Implemented | Delta |
|---|---|---|
| `GET /admin/datasets?q=&status=&offset=&limit=` → `{items, total, offset, limit}` | same + `links.next/prev`; `kind=` filter; `limit ≤ 200` | `DatasetSummary` also has `access` |
| `GET /admin/datasets/{id}` → summary + `versions[]` | same | each version also has `catalog_status`, `upload_status`, `upload_id`, `original_filename`, `published_at`, `error`, `actions[]` (`validate`/`publish`/`discard` currently allowed); `validation.ok` is `null` while validating; plus `recent_jobs[]` |
| `POST /admin/datasets/uploads` multipart `file` + `metadata` → 202 `AdminJob` | same; also `POST /admin/datasets` (alias) | **200** (not 202) with `result.outcome: "unchanged"`/`"already_uploaded"` when the identical file is re-uploaded: no new job, your `track()` sees `status: completed` immediately. Part order does not matter (you send `file` first: supported, streamed) |
| 409 `VERSION_EXISTS` / `CHECKSUM_CONFLICT`, 413 `FILE_TOO_LARGE`, 415, 422 `INVALID_METADATA` | same | also 409 `KIND_CONFLICT`, 422 `CHECKSUM_MISMATCH`, 507 `STORAGE_FULL`; `CHECKSUM_CONFLICT.details.suggested_version` |
| `POST …/versions/{v}/validate`, `…/publish` → 202 / 409 `INVALID_STATE` | same | 409 `READY_IMMUTABLE` for ready versions, 404 `VERSION_NOT_FOUND`; new `DELETE …/versions/{v}` (discard, 204) and `PUT /admin/datasets/{id}/access` |
| `GET /admin/jobs/{id}` → `AdminJob` | same | `progress` is `null` (indeterminate) during importer steps; `progress_message`, `attempts`, `upload_id` added; `GET /admin/jobs` lists |
| statuses `staged, validating, validated, publishing, ready, failed` | same | an upload starts **validating automatically** (the `upload` job is the validation); `staged` only appears for catalog rows imported by the CLI |

Please align two client-side checks in `validateUpload` with the server:

* `dataset_id` must be **lowercase** `^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$` (the importer and catalog
  constraint); uppercase gets 422 `INVALID_METADATA`.
* `source` may be omitted (the server records "admin upload via the CyberGIS application API by <user>" and returns a
  warning), but please encourage a real source description.

## 3. Exports — matches

| Your pin | Implemented | Delta |
|---|---|---|
| `GET /api/exports/capabilities` | same shape; public (200 anonymous) | adds `aoi`, `resampling`, `notes`, `limits`, `output_crs.native_default/rules` |
| `POST /api/exports` → 202 `ExportJob` | same for queued jobs | **201 with `status: completed`** for a full native export (the published COG/GeoJSON is streamed directly) — your code already handles `status === "completed"` on the POST response |
| `GET /api/exports/{id}` | same | `status` may also be `expired`; `result` has `sha256`, `metadata_url`, `expires_at`, `warnings[]`, `output.pixels_in_aoi/native_count_valid` for clips |
| `POST …/cancel` when `links.cancel` present | same (200 `ExportJob`; 409 `JOB_FINISHED`) | `links.cancel` is present while `queued`/`running` |
| `GET …/download` attachment | same; HEAD and Range supported; `X-Checksum-SHA256`, `Repr-Digest` | 410 `EXPORT_EXPIRED` after retention, 403 if the dataset was restricted meanwhile |

Formats: rasters `GTiff`, `COG`; vectors `GeoJSON` (case-insensitive aliases accepted). Any 2D `EPSG:<code>`.
`kind=clip` requires `aoi` (+`aoi_crs`, default EPSG:4326); `kind=full` refuses an `aoi` (422 `AOI_NOT_ALLOWED`).

## 4. Public catalog metadata — implemented

`GET /api/catalog/datasets` returns `{items, total, offset, limit, links}` with every field of your
`PublicDatasetMetadata` (plus `bbox`, `width`, `height`, `dtype`, `checksum_sha256`, `access`, `provenance`).
Visibility follows the dataset access level: **anonymous callers only see `public` datasets, and the default
level is `users`**, so an anonymous page load gets `200 {"items": [], "total": 0}` (no console error). Re-fetch it
after sign-in. `?limit=` max 500; follow `links.next` for more.
