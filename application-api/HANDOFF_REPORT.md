# Handoff report — Agent 05 (auth, administration, exports)

| Field | Value |
|---|---|
| Role | auth, administration, exports |
| Branch | `completion/agent5-application-api` |
| Frozen base | `c4dac59433f7aed90b8223ef6ba00cf4fef6449b` (verified equal to `origin/main` before branching) |
| Implementation SHA | `PENDING` (recorded in the follow-up commit that adds this SHA) |
| Completion status | **PASS for B1, B2, B3** with evidence: local suite (real PostGIS, real nginx, real inputs) **and** the full Docker Compose stack with the overlay (images built, all services healthy, 22-step live check incl. OGC discovery, existing Phase 2 e2e suite 92 passed / 0 failed). Open: Agent 4 merge of R1–R3/R6, decision D-A5-1; CITE/QGIS/browser runs belong to Agents 4/6/7 |

## 1. Changed paths (ownership)

Only `application-api/**` (new, Agent 5). No file of another owner was edited. Local, git-ignored, not committed:
the root `.env` (created with `infra/scripts/init-env.sh` + `application-api/scripts/init-env.sh`) and
`data/real-samples/` (downloaded test inputs).

```
application-api/
  src/cgis_app/            service, workers, migrations (0001 schema, 0002 roles), CLI
  tests/                   unit, auth, authorization, admin, exports, gateway, cli, real data, examples, bench
  docs/                    API.md, SECURITY.md, QGIS.md, RESPONSE_TO_AGENT6.md, openapi.json, examples/, evidence/
  deploy/                  compose.app-api.yaml (overlay), gateway/default.conf, gateway/default.protected.conf
  scripts/                 run_tests.sh, with_temp_postgis.sh, init-env.sh, fetch_real_samples.sh, check_manifest_compat.py
  Dockerfile (+.dockerignore), pyproject.toml, .env.example, README.md, CHANGE_REQUEST_FOR_AGENT4.md, HANDOFF_REPORT.md
```

## 2. Requirements (shared/ACCEPTANCE_MATRIX.md)

| ID | Evidence | Status |
|---|---|---|
| **B1** admin/user auth enforced server-side; direct user upload/publish forbidden, admin allowed; password/session/security tests | `test_auth.py` (25), `test_authorization.py` (53: 11 admin routes × anonymous 401 / user 403 / admin allowed, 8 header spoofs, 9 path variants, body escalation), `test_gateway.py` (5, through real nginx incl. `auth_request` variant), `test_cli.py` (2); `docs/SECURITY.md` | **PASS** (local + Docker stack) |
| **B2** admin upload → validate → publish → version status for actual geospatial files; size/path/MIME validation; async | `test_admin.py` (32) through the **existing importer** running out-of-process on `ingestion/requirements.txt`; real WorldPop 1 km India 2011 GeoTIFF and real geoBoundaries ADM1 GeoJSON in `test_real_data.py` (2); bombs, traversal, oversize, wrong types, bad CRS, corrupt/truncated TIFF, invalid geometry, conflicts, idempotency | **PASS** (local + Docker stack: published in the ingest-worker container, OGC API collection visible after 12 s) |
| **B3** full and clipped export, dataset/version, output CRS, per-kind formats, streamed large downloads, metadata/provenance | `test_exports.py` (32): native COG/GeoJSON streaming with Range/HEAD/checksums, GTiff/COG reprojection, raster clip (values compared to source), vector clip/reprojection, version selection, expiry, cancel, crash recovery; real-data clip and national reprojection; memory benchmark | **PASS** (local + Docker stack: token download with checksum, clip in the export-worker container) |
| U3 (Agent 6) | contract matched to Agent 6's provisional pin (`docs/RESPONSE_TO_AGENT6.md`) | dependency delivered; browser run is Agent 6/7 |

## 3. Decisions taken and decisions requested

| ID | Decision | Default | Needs |
|---|---|---|---|
| D-A5-1 | Protect `/ogc/`, `/tiles/`, `/api/operations`, `/api/downloads` with sign-in? | **No** in `deploy/gateway/default.conf` (Phase 2 behaviour, anonymous CITE and existing e2e keep working); `default.protected.conf` provided and tested | **Agent 7 / human**: choose per deployment. Recommendation: protected whenever restricted data is published |
| D-A5-2 | Who may discover datasets without a policy row | `APP_DEFAULT_DATASET_ACCESS=users` (anonymous sees nothing; `public` = explicit demo-only switch); exports always need sign-in | confirm |
| D-A5-3 | Browser auth = cookie session + synchronizer CSRF token; scripts = bearer; QGIS/GDAL = personal access tokens (Bearer or Basic with token as password) | as implemented | — |
| D-A5-4 | Full native export streams the published lossless COG for both `COG` and `GTiff` requests (a COG is a GeoTIFF); reprojection uses nearest neighbour and is flagged "totals NOT preserved" | as implemented | — |
| D-A5-5 | Additional formats (GeoPackage, Shapefile, NetCDF) not offered: not verified | — | ask if needed |

No scientific/model decision was made by Agent 5.

## 4. Cross-agent requests

* **Agent 4** — `CHANGE_REQUEST_FOR_AGENT4.md`: R1 gateway routes (ready file `deploy/gateway/default.conf`, tested
  with nginx), R2 Compose services/volumes (ready overlay `deploy/compose.app-api.yaml`, `config` validated), R3 root
  `.env.example` block, R4 read-route protection decision, R5 optional OGC filtering of `admins` datasets; also add
  `cgis_app` to `PROJECT_SCHEMAS` in `infra/db/provision_roles.py`.
* **Agent 6** — `docs/RESPONSE_TO_AGENT6.md`: their pinned shapes are implemented; deltas are additive (201 for
  native exports, 200 for idempotent uploads, lowercase `dataset_id`). Set `applicationApi.enabled=true` and
  `catalogMetadata="/api/catalog/datasets?limit=500"` after integration.
* **Agent 7** — decide D-A5-1; re-run `scripts/live_check.py` and the module suite on the integrated stack.

## 5. Tests (exact commands, numbers, environment)

Environment: Linux 6.8 x86_64, 16 CPUs, 15 GiB RAM; PostgreSQL 17.6 + PostGIS 3.5 (conda-forge, throwaway cluster);
nginx 1.31.8 (the gateway image pins 1.27); API interpreter Python 3.12.15 with `pyproject.toml` pins (fastapi 0.162.0,
rasterio 1.5.2); ingest worker interpreter Python 3.12.15 with `ingestion/requirements.txt` (rasterio 1.4.3, rio-cogeo 5.4.1).

```sh
PG_BIN=<pg bin> PYTHON=<api venv>/bin/python APP_TEST_INGEST_PYTHON=<ingest venv>/bin/python \
APP_TEST_NGINX=<nginx> sh application-api/scripts/run_tests.sh -v -rA
# -> 186 passed, 1 skipped (benchmark) in 85 s          log: docs/evidence/test-run.txt
APP_BENCH=1 APP_BENCH_SIZE=8000|16000|24000 … run_tests.sh tests/test_bench.py   # 3 runs, all passed
```

| Suite | Passed | Failed | Skipped |
|---|---|---|---|
| test_unit (no DB) | 34 | 0 | 0 |
| test_auth | 25 | 0 | 0 |
| test_authorization | 53 | 0 | 0 |
| test_admin | 32 | 0 | 0 |
| test_exports | 32 | 0 | 0 |
| test_gateway (real nginx) | 5 | 0 | 0 |
| test_cli | 2 | 0 | 0 |
| test_real_data | 2 | 0 | 0 |
| test_examples | 1 | 0 | 0 |
| test_bench | (3 separate runs passed) | 0 | 1 in the main run |
| **Total** | **186** | **0** | **1** |

Dedicated security suites: 25 auth + 53 authorization + 5 gateway = **83** tests. Further security cases live in
`test_unit` (hashing, tokens, metadata sanitization, path/symlink safety, 11 upload-stream rejections, config
redaction), `test_admin` (bombs, traversal, wrong types, oversize, metadata injection, access policy) and
`test_exports` (owner isolation, restricted datasets, path-like ids).

Other checks: `docker compose -f compose.yaml -f application-api/deploy/compose.app-api.yaml config --quiet` → OK;
clean non-editable install of the `api` image's dependency set → `pip check` OK, 31 unit tests passed;
`scripts/check_manifest_compat.py` on Agent 1 (`8475ba3`) and Agent 2 (`996d71a`) manifests → 30/30 entries
(including the 11 national `india-l1-pop-2001…2011`) accepted unchanged (`docs/evidence/manifest-compat.txt`).

### Docker Compose stack (Docker 29.9.0, Compose v5.6.0)

```sh
docker compose -f compose.yaml -f infra/compose.db-port.yaml -f application-api/deploy/compose.app-api.yaml --profile tools build
docker compose -f compose.yaml -f infra/compose.db-port.yaml -f application-api/deploy/compose.app-api.yaml up -d --wait
python application-api/scripts/live_check.py                      # uses the bootstrap accounts from .env
docker compose … -f tests/e2e/compose.e2e.yaml run --rm -T ingestion seed
docker compose … -f tests/e2e/compose.e2e.yaml run --rm -T e2e -ra -m 'not persistence and not performance'
docker build -f application-api/Dockerfile --target test .
```

| Check | Result | Evidence |
|---|---|---|
| Build of all 10 images incl. `api`, `ingest-worker`; `test` target (unit tests in the image) | exit 0 / exit 0 | `docs/evidence/docker-stack.txt` |
| `up -d --wait`: 9 long-running services healthy, 4 one-shots exit 0 | PASS (after fixing the worker healthcheck, below) | `docker-stack.txt` |
| DB accounts: owner, 5 service login roles, 6 group roles, `cgis_app` migrations 0001–0002; API role cannot write the catalog | PASS | `docker-stack.txt` |
| Live end-to-end through the real gateway (22 steps: 401/403 matrix, admin upload of the real WorldPop file → validate → publish in the worker container, OGC collection after 12 s, token download + checksum, clip in the export worker, legacy `/api/operations/clip` unchanged, revoked token refused) | 22/22 PASS | `docs/evidence/live-stack-check.json` |
| Existing Phase 2 e2e suite with the overlay and proposed gateway | 92 passed, 10 skipped (steps only `run_e2e.sh` performs), 0 failed | `docs/evidence/e2e-regression.txt` |
| Idle memory: app-api 152 MiB, ingest worker 166 MiB, export worker 91 MiB | — | `docker-stack.txt` |

Defects found by the Docker run and fixed: (1) the export worker reused the API image's HTTP healthcheck and was
reported unhealthy, so workers now have `cgis-app worker-health` (file touched by the poll loop and job heartbeats);
(2) after rebuilding `app-api`, the gateway kept the old container IP (502) until `docker compose restart gateway`.
This is a property of the existing gateway (R6 in the change request), documented in the README.

**Not run by Agent 5:** OGC CITE and headless QGIS against the protected gateway variant, and the browser suite.

## 6. Real inputs versus synthetic tests

| Input | Origin, license | SHA-256 | Used for |
|---|---|---|---|
| `ind_ppp_2011_1km_Aggregated.tif` (19 087 766 B, 3508×3451 float32, EPSG:4326, NoData −99999) | WorldPop Global 2000–2020 1 km unconstrained, IND 2011, CC BY 4.0 | `3a058a6e5cd42c9d18cf8c77e0eeb5f09dbc84a3053e963b981147fb67769703` | upload → importer validation (sum 1 211 157 895, 4 010 402 valid cells) → publish (COG) → lossless native download → Hyderabad clip (values identical to source; 1 504 valid cells, sum 7 260 494.76) → national reprojection to EPSG:32644 (3646×3693 COG; sum 1 194 946 642, −1.3 %: nearest-neighbour warning confirmed) |
| `geoBoundaries-IND-ADM1_simplified.geojson` (5 103 747 B, 36 features) | geoBoundaries gbOpen IND ADM1 release 9469f09, CC BY 2.5 IN | `4c63fe43294a391e8f2de4e9f86f3edb60f8688275b9fee90c61fb2aa0c26061` | upload → validation → publish into `geo.admin_units` → clip around Hyderabad returns Telangāna |

Fetched and checksum-verified by `scripts/fetch_real_samples.sh` into git-ignored `data/real-samples/`. These are
**software test inputs**, not CyberGIS products. Everything else is generated SYNTHETIC test data
(`tests/synthetic.py`), marked SYNTHETIC in titles and `provenance.synthetic`. Evidence:
`docs/evidence/real-data-evidence.json`.

## 7. Performance, memory and storage (16 GB target)

Each worker in its own process, peak RSS from `/usr/bin/time` (`docs/evidence/bench-*.json`; synthetic raster,
float32, DEFLATE, 1/8 NoData):

| Cells (uncompressed) | validate (importer) | publish (COG) | reproject to EPSG:32644 COG |
|---|---|---|---|
| 64 M (256 MB) | 1.3 s, 372 MiB | 6.4 s, 579 MiB | 6.8 s, 357 MiB |
| 256 M (1 GB) | 4.0 s, 372 MiB | 23.6 s, 590 MiB | 25.5 s, 359 MiB |
| 576 M (2.3 GB) | 8.4 s, 372 MiB | 51.3 s, 583 MiB | 57.5 s, 358 MiB |

Peak memory stays flat while the raster grows 9×: work is block/strip-wise and GDAL's cache is pinned to
`APP_GDAL_CACHEMAX_MB` (without the pin GDAL used 5 % of RAM: the first run peaked at 1170 MiB). A native COG download of
the 1 GiB raster allocated 0.4 MiB of Python memory (streamed from disk). Extrapolation (not measured): a national
~100 m India grid (~1.3 G cells) should publish in ≈2 min at the same peak; the published asset would be ≈ the
compressed source size. Staging and exports are capped by `APP_STAGING_MAX_TOTAL_BYTES` / `APP_EXPORT_MAX_TOTAL_BYTES`
(32 GiB each) with a 2 GiB free-space floor; exports expire after 24 h. Proposed container limits: app-api 768m,
ingest worker 2g, export worker 1536m.

## 8. API, schema and examples

* OpenAPI 3.1: `application-api/docs/openapi.json` (27 paths), live `/api/app/openapi.json`, `/api/app/docs`.
* Reference with flows, status codes, error codes, job states, limits: `docs/API.md`.
* 25 recorded request/response pairs from a real run (secrets and ids scrubbed): `docs/examples/`.
* Upload example: `docs/API.md` §4; export examples §6; QGIS/GDAL tokens: `docs/QGIS.md`.

## 9. Integration instructions

1. Merge the branch (only `application-api/**`).
2. Apply `CHANGE_REQUEST_FOR_AGENT4.md` R1–R3 and R6 (or, until then, use the overlay:
   `docker compose -f compose.yaml -f application-api/deploy/compose.app-api.yaml --profile tools build && … up -d --wait`).
3. `sh application-api/scripts/init-env.sh` (adds `APP_*` to `.env`; bootstrap admin = `APP_BOOTSTRAP_ADMIN_*`).
4. Check: `curl -s localhost:8080/api/app/ready`; sign in with the bootstrap admin; upload a manifest-described file;
   publish; export. Test accounts for automated suites: `docker compose run --rm app-api cgis-app fixtures --out …`.
5. Decide D-A5-1; if protected, swap in `default.protected.conf` and give CITE/QGIS a personal access token.
6. Backward compatibility: no existing route, table or behaviour changes. The service reads the frozen catalog
   contract and adds its own `cgis_app` schema; the importer and processing code are used unchanged.

## 10. Known limitations and risks

* OGC discovery of a newly published dataset relies on pygeoapi's catalog refresh (`OGC_REFRESH_INTERVAL`, 30 s);
  12 s were measured on the live stack.
* After rebuilding/recreating `app-api`, restart the gateway (R6).
* Per-dataset `admins` access is enforced by this service only; OGC/Tiles still publish every ready dataset (R5/D-A5-1).
* Login lockout can be abused to lock an account for 15 min; no TLS by default (localhost); CSRF tokens stored in clear
  (see `docs/SECURITY.md` "Known limitations").
* Reprojected exports use nearest neighbour; totals of count rasters change (measured −1.3 % on real WorldPop 1 km).
  Area-weighted/mass-preserving reprojection is not implemented.
* Vector exports read the GeoJSON asset in memory (refused above 2 × `APP_MAX_VECTOR_UPLOAD_BYTES`; use OGC API
  Features for larger layers). Only GTiff/COG/GeoJSON are offered.
* The national 100 m WorldPop source (1.85 GB) was not pushed through the API on this machine (13 GB free disk);
  the 576 M-cell benchmark and the real 1 km file stand in for it.
