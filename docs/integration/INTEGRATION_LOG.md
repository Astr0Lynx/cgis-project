# Integration log: `phase2/integration`

This is the record of how the five feature branches were combined: which commits, in what order,
every conflict, every change made to another owner's files and why, and how each proposed
contract change was decided. Test evidence is in [`FINAL_REPORT.md`](FINAL_REPORT.md).

## 1. Inputs verified

| Ref | SHA | Check |
|---|---|---|
| default branch | `main` @ `264bce05ab149851d750b56cdf0e0275c60d33be` | `git ls-remote --symref origin HEAD` → `refs/heads/main`; ancestor of `phase2/base`; unchanged since the baseline |
| `phase2/base` (BASE_SHA) | `cc395c69ee738b4597684cf2ccfb84254c15723c` | The single baseline commit on top of `main` |
| `phase2/agent1-infrastructure` | `201c575e3aaf8ee622c2e4aef83738151fda2502` | Parent is BASE_SHA; see the note below |
| `phase2/agent2-postgis` | `98003efdf50f4aa5021944629bf33aaea57dd561` | merge-base with base = BASE_SHA; 2 commits; `database/HANDOFF_REPORT.md` |
| `phase2/agent3-ingestion` | `35f16efa86ee995d9e1404b30d1fd1121d901d0f` | merge-base = BASE_SHA; 2 commits; `ingestion/HANDOFF_REPORT.md` |
| `phase2/agent4-ogc` | `1f13e4f9e92d5beafc80d6024f033e34830eac09` | merge-base = BASE_SHA; 2 commits; `ogc-services/HANDOFF_REPORT.md` |
| `phase2/agent5-processing` | `315e503f4a0486acd354bb4b51b29438a440df32` | merge-base = BASE_SHA; 2 commits; `spatial-processing/HANDOFF_REPORT.md` |

**Agent 1 note.** When integration started, `phase2/agent1-infrastructure` did not exist on
GitHub. The baseline stage had been pushed, but the infrastructure stage had not. The operator
supplied Agent 1's package and asked this session to "check Agent 1's work and finish whatever
hasn't been done". The infrastructure stage was then implemented on its own branch, at BASE_SHA,
within Agent 1's scope and instructions. It is recorded in `infra/HANDOFF_REPORT.md`, pushed, and
only afterwards merged here. The branch therefore has its own provenance, like the other four, but
the same session wrote it and integrated it.

## 2. Merge sequence

`git worktree add -b phase2/integration <worktree> cc395c6`, then `git merge --no-ff` in the
order given in `contracts/RUN_ORDER.md`:

| # | Merge commit | Merged | Result |
|---|---|---|---|
| 1 | `2854d38` | `phase2/agent1-infrastructure` @ `201c575` | clean |
| 2 | `bf2b38f` | `phase2/agent2-postgis` @ `98003ef` | clean |
| 3 | `973e0f5` | `phase2/agent3-ingestion` @ `35f16ef` | clean |
| 4 | `694fd27` | `phase2/agent4-ogc` @ `1f13e4f` | clean |
| 5 | `4436de5` | `phase2/agent5-processing` @ `315e503` | clean |

**No textual conflicts.** Every agent stayed inside its own directories. The only shared file,
`docs/contract-change-requests/README.md`, was unchanged on all four module branches.
`contracts/` is byte-identical to the baseline (checked against the SHA-256 list in
`contracts/README.md`). No commit was cherry-picked, rewritten or dropped.

## 3. Integration fixes (made after the merges, in `phase2/integration` commits)

Each change is the smallest one that makes the merged system work. Owners should review the rows
for their module.

### 3.1 Product changes

| # | File(s) (owner) | Problem found by | Change | Why |
|---|---|---|---|---|
| F1 | `infra/migrate/Dockerfile` (1) | First `docker compose up`: `unknown migration track 'core' (looked in …/site-packages/migrations/core)` | Set `CGIS_DB_MIGRATIONS_DIR=/opt/cgis/database/migrations` | `cgis_db` finds its SQL relative to the package source. Once pip-installed, it needs the documented override. |
| F2 | `spatial-processing/Dockerfile` (5) | `processing` crash loop: `ImportError: libexpat.so.1` | `apt-get install libexpat1` in the `base` stage | The GDAL inside the rasterio wheel needs libexpat, and slim images lack it. Agents 3 and 4 already do this. Agent 5 could not build images on its machine (see its report). |
| F3 | `ogc-services/pygeoapi/cgis_ogc/publish.py`, `…/pygeoapi_plugins/postgresql.py` (4) | Agent 4's `test_openapi_document_is_valid_and_complete` against the real schema: `Duplicate parameter 'properties'`. QGIS OAPIF layer invalid. | Publish only `OGC_VECTOR_PROPERTIES` (default `unit_id,name,level`, the frozen v1 columns). The provider subclass trims pygeoapi's `fields` to that list. | Agent 2's additive `geo.admin_units.properties` (JSONB) became a feature property and queryable whose name collides with the standard `properties` query parameter, making `/openapi` invalid. Internal columns (provenance, timestamps) were also leaking into features. |
| F4 | `infra/gateway/default.conf` (1) | Headless QGIS check: OAPIF layer not valid when the URL is `…/ogc` | `/ogc`→`/ogc/` (and the other prefix redirects) now return an absolute `Location` built from `$scheme://$http_host` | QGIS's OAPIF provider does not follow the relative `Location` the infrastructure branch emitted. `$http_host` keeps the published port, which a plain absolute redirect would not. |
| F5 | `.env.example`, `infra/db/provision_roles.py` (1) | Agent 2's raster-demo tests: `PostGIS Raster must be in the same schema as PostGIS (public)` | Default owner is now `cgis_admin` (was `cgis`). `provision_roles.py` refuses an owner or service name equal to a project schema. | With the owner named `cgis`, the default `search_path` `"$user", public` resolves to Agent 2's `cgis` helper schema, so unqualified `CREATE EXTENSION postgis_raster` landed there. Agent 2 had tested as `postgres`. |
| F6 | `compose.yaml` (1) | Reading `ingestion/docs/PHASE1_HANDOFF.md` | `ingestion` mounts `./data/incoming:/data/incoming:ro` | The Phase 1 import commands in that document use `/data/incoming/<delivery>`, which no service mounted. |
| F7 | `Makefile`, `README.md`, `infra/README.md`, test scripts (1, 6) | Agent 3 suite ran stale tests | `make up` and the scripts run `docker compose --profile tools build` first | `up --build` never rebuilds the tool-profile `ingestion` image, so `seed` could run old code after a change. |
| F9 | `infra/migrate/Dockerfile`, `infra/db/wait_for_db.py` (1); restart docs, `Makefile`, `run_e2e.sh` (1, 6) | Fresh-clone e2e: after `docker compose restart`, `migrate` exited 1 and `up --wait` failed | `migrate` retries the DB connection for up to 60 s. The documented restart and the e2e step use `down` + `up -d --wait`. | `restart` also restarts the one-shot `migrate` at the same moment as `db`, so it raced the database coming back up. `down` + `up` recreates every container in dependency order, and is the stronger persistence test. |
| F8 | `demo-client/app.js` (6) | e2e: OGC tiles at z14 are 404 (the tileset limits end at z13 for the 64×64 fixture) | The viewer takes the source `minzoom`/`maxzoom`/`bounds` from `tileMatrixSetLimits` (OGC) or TileJSON (vendor) | Above the advertised limits MapLibre must over-zoom the deepest level instead of requesting tiles the server rightly refuses. |

### 3.2 Test-only changes in other modules (the tests assumed stand-in schemas)

| # | File (owner) | Failure on the integrated schema | Change |
|---|---|---|---|
| T1 | `ogc-services/tests/integration/conftest.py` (4) | `datasets_ready_complete` check violation: test rows had no checksum | `insert_dataset` writes a deterministic 64-hex checksum |
| T2 | `ogc-services/tests/integration/test_tiles_vendor.py` (4) | `datasets_asset_path_safe` refuses `../`, absolute, URL and `/vsicurl/` paths at INSERT | The test accepts the DB-level refusal as the outcome. Paths the DB accepts (symlink escape, disguised VRT, missing file) are still tested against the tiler, using Agent 4's stub hostile files. |
| T3 | `ogc-services/tests/integration/test_publication.py` (4) | `[] == ['town-b']`: with Agent 2's `dataset_id` column the publisher scopes by `dataset_id` (CCR-4.1, preferred over the provenance hint by design) | When the column exists, the test inserts and removes its own unit row for the new dataset |
| T4 | `ingestion/tests/test_import_db.py` (3) | `TRUNCATE catalog.datasets` blocked by Agent 2's FK from `geo.admin_units` | Truncate both tables together, as the suite's own fixture already does |
| T5 | `ingestion/tests/test_import_db.py` (3) | `ADD COLUMN dataset_id`: the column already exists, and the `finally` block would have dropped Agent 2's real columns | Add and drop the columns only when absent (the stand-in schema) |
| T6 | `spatial-processing/tests/test_postgis.py` (5) | `datasets_checksum_format` (32-hex uuid) and `datasets_ready_complete` (no resolution) | 64-hex checksum and `resolution_x/y = 0.001` in the test rows |

All test changes keep the original assertions. They only make the setup valid under Agent 2's
stricter constraints, which are themselves extra protection: hostile asset paths cannot even be
stored.

## 4. Contract change requests: decisions

No coordinated v2 contract was needed. Everything integrates on the frozen v1 contract plus
Agent 2's documented additive columns.

| Request | Decision |
|---|---|
| Agent 2 A1–A4 (additive columns and constraints) | **Accepted as implemented.** The e2e tests check the contract columns and that the view matches the table column for column. |
| Agent 2 B1: version-aware unit identity | **Deferred to Phase 3/4.** Breaking for Agents 3, 4 and 5. A boundary dataset's units are replaced in place by a new version, as Agent 3's CCR-3.4 documents. |
| Agent 2 B2: `catalog.datasets.status_message` | **Not adopted.** Failure details live in `provenance.ingestion.error` (CCR-3.3). |
| Agent 3 CCR-3.1: link units to dataset | **Already satisfied** by Agent 2's `dataset_id`, `dataset_version`, `properties` and `source`. The importer fills them, as the e2e test `test_admin_units_linked_to_catalog_dataset` checks. |
| Agent 3 CCR-3.2: `units` nullable for vectors | **Satisfied.** Nullable, and required only for ready rasters. |
| Agent 3 CCR-3.3 / 3.4 | **Accepted** as documented clarifications. |
| Agent 4 CCR-4.1: `admin_units.dataset_id` | **Satisfied** by Agent 2's column. The publisher scopes vector collections by it. |
| Agent 4 CCR-4.2: read-only DB role | **Implemented.** `ogc` and `tiles` log in as a `cgis_reader` member with `default_transaction_read_only=on`. |
| Agent 5 §6: `ops.jobs.result` | The column exists (Agent 2, additive), but the processing service does not write it yet. Results stay in `result.json` on the outputs volume. **Follow-up for Agent 5.** |
| Agent 5 §4: unit vocabulary | **Accepted.** The fixtures use `people_per_cell`, and Agent 3's Phase 1 guide requires explicit density units. |

## 5. Owner notifications

There is no messaging channel between agents in this setup. These tables, plus the comments in
each changed file that start with "Integration fix (Agent 6)" or "Integration (Agent 6)", are the
notification. Before Phase 3 starts, the owners should review F2 and T6 (Agent 5), F3, T1, T2 and
T3 (Agent 4), T4 and T5 (Agent 3), and F5 (Agent 2: the raster track's assumption about the
migrating role).
