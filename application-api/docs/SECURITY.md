# Security design and review notes (application-api)

Scope: authentication, authorization, admin uploads/publication, exports and the proposed gateway routes.
Each control names the test that exercises it (`tests/…`). Counts are in `HANDOFF_REPORT.md`.

## Identity

| Control | Implementation | Tests |
|---|---|---|
| Password storage | argon2id (argon2-cffi RFC 9106 low-memory profile: t=3, m=64 MiB, p=4); DB `CHECK` refuses anything that is not an argon2id PHC string; transparent rehash on parameter change; at most `APP_HASH_CONCURRENCY` (2) hashes at once | `test_unit::test_password_hash…`, `test_auth::test_passwords_stored_hashed` |
| No default credentials | none in code, images or Git; bootstrap accounts only from `APP_BOOTSTRAP_*` (or `*_FILE`); `init-env.sh` generates random values; weak/placeholder passwords refused; DB role passwords must be ≥16 chars and not placeholders | `test_unit::test_password_policy`, `test_login_role_config_is_checked` |
| Session secrets | 256-bit `secrets.token_urlsafe`, typed prefixes (`cgis_s_`, `cgis_b_`, `cgis_pat_`); only SHA-256 digests stored; a secret is valid only in its own channel (cookie vs Authorization) | `test_auth::test_credentials_only_valid_in_their_channel` |
| Cookie | `cgis_session`: HttpOnly, SameSite=Strict, Path=/, Secure automatically on https (`APP_COOKIE_SECURE`) | `test_auth::test_login_sets_hardened_cookie…` |
| CSRF | synchronizer token returned by login and `/auth/me`, required as `X-CSRF-Token` on every unsafe method of a cookie session, compared in constant time; plus `Origin`/`Sec-Fetch-Site` same-origin check; login itself refuses foreign `Origin`; JSON bodies only (form posts refused) | `test_auth::test_csrf_and_origin…`, `test_login_csrf…`, `test_login_requires_json` |
| Expiry / revocation | absolute TTL + idle timeout; logout, password change (other sessions + tokens), role change, disable revoke; expired/revoked → 401 with a specific code | `test_auth::test_session_expiry…`, `test_logout_revokes…`, `test_password_change…`, `test_role_change…`, `test_disabled_account` |
| Brute force | per-username (5/15 min) and per-address (50/15 min) lockouts with `Retry-After`; uniform error for unknown user vs wrong password; constant work for unknown users (dummy hash) | `test_auth::test_lockout…`, `test_bad_credentials_are_indistinguishable` |
| Secrets in URLs | query parameters named like credentials → 400 before routing; tokens only in headers/cookies | `test_auth::test_secrets_in_url_refused` |
| Logs | path-only access log (no query strings), usernames and outcomes only; DSNs redacted; no passwords, tokens, cookies or CSRF tokens | `test_auth::test_no_secrets_in_logs`, `test_unit::test_settings_from_env_and_redaction` |
| Interoperable tokens | personal access tokens (scope `read` by default = role user; `admin` scope only for admin accounts), ≤ `APP_TOKEN_MAX_TTL_DAYS`, listable, revocable; HTTP Basic accepts **only** tokens, never account passwords; tokens cannot mint tokens | `test_auth::test_personal_access_tokens`, `test_basic_auth_never_accepts_passwords`, `test_user_cannot_create_admin_scoped_token` |

## Authorization

* Enforced per route in the service (FastAPI dependencies `require_user` / `require_admin`), never by path
  prefix or a header set by someone else. Spoofed `X-Role`, `X-Cgis-Role`, `X-Forwarded-User`, `X-Original-URL`,
  `X-HTTP-Method-Override`, case/slash/dot-segment path variants never reach an admin handler
  (`test_authorization.py`, 11 admin routes × anonymous/user/admin, 8 header spoofs, 9 path variants).
* Request bodies forbid unknown fields, so `role`, `owner`, `status`, `file`, `asset_path` cannot be injected.
* Datasets carry an access level (`public|users|admins`, default `users`); hidden datasets answer 404 in
  discovery and exports, and a download re-checks the level (403 after a dataset is restricted)
  (`test_admin::test_access_policy…`, `test_exports::test_dataset_restricted_after_export`).
* Export jobs are private to their owner (404 for others); admins may list all (`test_exports::test_jobs_are_private…`).
* Gateway: the proposed `deploy/gateway/default.protected.conf` gates `/ogc/`, `/tiles/`, `/api/operations`,
  `/api/downloads` with `auth_request` → `GET /auth/verify`, including CSRF for cookie POSTs
  (`test_gateway::test_protected_variant_gates_read_routes`). Whether that variant is the default is decision
  D-A5-1 (HANDOFF_REPORT.md).

## Uploads (admin only)

* Authentication and the admin role are checked **before** the body is read; anonymous/user uploads never touch
  disk (`test_authorization::test_user_gets_403` asserts an empty staging dir).
* Streaming multipart parser: no spooling to /tmp, the file part is written to `<staging>/<32 hex>.<tif|geojson>`
  created with `O_CREAT|O_EXCL|O_NOFOLLOW` relative to an `O_DIRECTORY|O_NOFOLLOW` root handle; the client file
  name is display-only (basename, sanitized). Exactly one `metadata` and one `file` part; any other field,
  nested multipart, `Content-Transfer-Encoding` or `Content-Encoding` → 415/422.
* Type is decided from the bytes (TIFF/BigTIFF magic or a JSON object) and must match the extension and the
  declared `kind`; archives and other formats are refused (no zip/gzip → no archive bombs, no shapefile path tricks).
* Byte budget enforced while streaming (also without Content-Length): rasters `APP_MAX_UPLOAD_BYTES`, GeoJSON
  `APP_MAX_VECTOR_UPLOAD_BYTES`; staging quota and free-space floor (507). Rejected or failed uploads leave no file.
* Decompression bombs: before the importer decodes pixels, the worker reads the TIFF header and refuses
  > `APP_MAX_RASTER_PIXELS`, > `APP_MAX_RASTER_UNCOMPRESSED_BYTES`, > `APP_MAX_RASTER_BANDS` bands or a
  compression ratio above `APP_MAX_COMPRESSION_RATIO`; GDAL is restricted to the GTiff driver (no VRT/XML that
  could reference other files or URLs) with `GDAL_DISABLE_READDIR_ON_OPEN`. GeoJSON nesting depth is bounded.
  (`test_admin::test_bomb_like_rasters…`, `test_wrong_file_types`, `test_oversize_and_encoded_bodies`).
* Metadata may not contain `file`, `asset_path`, `url` or `status`; the dataset id must be a lowercase slug
  (`test_admin::test_bad_metadata_rejected…`, `test_traversal_filename_is_display_only`).
* Publication runs the existing importer in the ingest worker: idempotent per (dataset, version, checksum),
  ready versions immutable, a failed import never touches a ready version (`test_admin::test_repeat_upload…`,
  `test_failed_publish_leaves_existing_ready_version`).

## Least privilege

| Process | DB role | Files |
|---|---|---|
| app-api | `cgis_app_api`: cgis_app tables + `cgis_reader` (catalog/geo **read-only**) | staging RW, assets RO, exports RO |
| app-ingest-worker | `cgis_app_worker`: jobs/uploads + `cgis_ingest` (catalog/geo writes); **no** access to users/sessions | staging RW, assets RW |
| app-export-worker | `cgis_app_worker` | assets RO, exports RW |
| app-migrate (one-shot) | database owner | — |

The tests connect with exactly these roles. No container gets the Docker socket; nothing runs Docker commands.

## Exports

* Only catalog-backed ids; asset paths come from the catalog and are re-validated (relative, no `..`, no scheme,
  no `/vsi`, regular file, symlink escape refused) (`test_unit::test_resolve_asset_rejects_escapes`).
* Native downloads stream from disk in chunks (peak Python allocation asserted below a quarter of the size for a
  48 MB incompressible file; 0.4 MiB measured for the 13 MB COG of the 1 GiB benchmark raster); other outputs are bounded by pixel caps, size caps, time limit,
  per-user concurrency (429) and a volume quota (507), then expire.
* Metadata never includes asset paths, server paths, credentials, upload user names or full URLs (`source`
  links are reduced to their host) (`test_unit::test_public_provenance…`, `test_public_source…`).

## Security headers

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (not on the Swagger UI page),
`Cache-Control: no-store` on `/auth` and `/admin`, `private, no-store` on downloads.

## Known limitations / review notes

1. **Plain HTTP by default.** The local stack listens on `127.0.0.1:8080` without TLS, so the cookie is not
   `Secure` unless `PUBLIC_BASE_URL` is https. Put TLS in front (and set `APP_COOKIE_SECURE=true`) before exposing
   the gateway beyond localhost.
2. **Lockout as denial of service.** Five wrong passwords lock a username for 15 minutes, which an attacker can
   use to lock out the admin temporarily. Mitigations: tokens and existing sessions keep working; the CLI
   (`cgis-app users …`) is unaffected; tune `APP_LOGIN_MAX_FAILURES` / `APP_LOGIN_LOCKOUT_SECONDS`.
3. **CSRF tokens are stored in clear** in `cgis_app.sessions` (session secrets are hashed). A CSRF token alone
   grants nothing without the HttpOnly cookie.
4. **Staging quota is checked when an upload starts**, so N concurrent uploads can overshoot it by up to N×limit;
   the free-space floor is re-checked every 256 MiB while streaming.
5. **OGC/Tiles/processing reads stay public** with `default.conf` (unchanged Phase 2 behaviour, needed for
   anonymous CITE). Per-dataset `admins` access is enforced by this service only; a restricted dataset must not be
   published through OGC/Tiles until Agent 4 filters by access level or the protected gateway variant is used.
6. No MFA, no password reset by e-mail, no account self-registration (accounts are created by an operator).
