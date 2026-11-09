# Agent 6 completion handoff — CyberGIS viewer (`demo-client/`)

| | |
|---|---|
| Role | Complete the existing viewer (application layer, flowchart page 2) |
| Branch | `completion/agent6-webapp` |
| Frozen base | `c4dac59433f7aed90b8223ef6ba00cf4fef6449b` (verified equal to `origin/main` before branching) |
| Implementation tip SHA | `__IMPL_SHA__` (this report is committed on top of it; see `git log`) |
| Changed paths | `demo-client/**` only (ownership confirmed; no other agent's path touched) |
| Completion status | **PARTIAL** — software complete and tested against mocks; real-data and integrated-stack evidence is `NOT_RUN` (no Docker on this machine; Agent 5 API not yet published) |

## 1. Requirement IDs (shared/ACCEPTANCE_MATRIX.md)

| ID | What this branch delivers | Evidence here | Status from Agent 6's side |
|---|---|---|---|
| **U1** | National map discovers every Level 1 year and the Level 2 group from metadata only. India initial view. Paginated, bbox-scoped boundaries with a per-view budget (no 1,000-feature truncation, no country-wide GeoJSON) | `national.spec.js`, `boundaries.spec.js`, `unit/catalog.test.js`, `unit/logic.test.js`; screenshots `u1-*` | Software **PASS on mock**; real 11-year run **NOT_RUN** |
| **U2** | Year slider, swipe and side-by-side, shared legends across compatible `people_per_cell` layers, NoData transparency and legend, L1-vs-L2 only on common support (polygon totals), resolution and provenance warnings, pixel B − A only on comparable grids | `compare.spec.js`, `analysis.spec.js`, `unit/logic.test.js`; screenshots `u2-*` | Software **PASS on mock**; real data **NOT_RUN** |
| **U3** | Login/logout/role, admin upload → validate → publish → layer on map, read-only user (UI + server refusal tested), 401/403/expiry, full/clip export with dataset, version, CRS and format, progress, cancel, size and provenance; keyboard/labels/responsive | `auth-admin.spec.js`, `exports.spec.js`, `usability.spec.js`; screenshots `u3-*` | Software **PASS against the provisional Agent 5 contract**; real Agent 5 API **BLOCKED** (not published) |

These rows are evidence for Agent 7, not final verdicts. Per the matrix, mock runs prove software, not science.

## 2. Test commands and results (this machine)

Environment: Windows 11, Node 22.14.0, Playwright 1.49.1 (Chromium headless shell 131, SwiftShader GL),
viewport 1366×768 unless a test sets 375×760. No Docker engine is installed here, so the real stack was not run.

```sh
cd demo-client/tests && npm ci && npx playwright install chromium
node --test unit/*.test.js
npx playwright test            # starts two mock gateways itself (ports 8099 full, 8098 phase2)
```

| Suite | Result | Log |
|---|---|---|
| Unit (`node:test`) | **29 passed, 0 failed** (exit 0) | `tests/results/unit.txt` (git-ignored; regenerate) |
| Browser, mock "phase2" (port of `check_viewer.py` + NoData AOI) | **2 passed, 0 failed** | `tests/results/e2e.txt` |
| Browser, mock "full" (U1/U2/U3, usability) | **38 passed, 0 failed** (whole run: 40 passed, 0 failed, 0 flaky, 11.2 min; `auth-admin.spec.js` re-run on the final commit: 6 passed) | `tests/results/e2e.txt` |
| Browser, real stack (`--project=stack` + legacy) | **NOT_RUN** (no Docker) | command above with `CGIS_BASE_URL` |
| Agent 7's `test_60_viewer.py` / `check_viewer.py` | **NOT_RUN** here; static port passes (`unit/legacy-contract.test.js`), browser port passes on the Phase-2-shaped mock (`legacy.spec.js`) | |

Every browser test fails on any console error or HTTP ≥ 400 in its nominal path. The only deliberate
4xx responses come from negative tests: wrong password, AOI too large, unsupported CRS, user-role bypass,
and anonymous access.

## 3. Real-input evidence versus synthetic-only tests

- **Synthetic/mock only.** All browser runs use `tests/mock-gateway`: MOCK national fields, 6 MOCK
  towns, 12,000 MOCK boundary cells, and the exact Phase 2 synthetic fixtures (Town A 100/200 reproduced).
  Mock titles say MOCK/SYNTHETIC, and the provenance badge shows *SYNTHETIC test data* on every
  screenshot.
- **Real-shape check without real bytes.** `unit/catalog.test.js` parses the exact OGC collection shape
  that `ogc-services` would publish for Agent 2's real `india-l1-pop-2001` manifest (from
  `origin/completion/agent2-level1`, read-only). This found and fixed a real parsing bug: a source label
  with parentheses. No real raster was available locally.
- **Discovery is name-independent.** Mock ids (`mockindia-l1-YYYY`, `mockindia-l2-2011-<town>`) differ from
  the real convention (`india-l1-pop-YYYY`, `india-l2-pop-2011-<town>`). Unit tests use arbitrary ids, and
  a static test asserts that no module contains a dataset id or a fixture camera.

## 4. API compatibility and route integration

| Route family | Use | Standard? | Change |
|---|---|---|---|
| `/ogc/collections`, `/items`, `/conformance`, tileset metadata and tiles | discovery, boundaries, search, rasters, legends | OGC API – Features/Tiles | none; unchanged contract |
| `/tiles/datasets`, `/info`, `/statistics`, `/point`, tiles + `rescale` | metadata gaps, shared range, pixel readout, alternative renderer | **vendor-specific (TiTiler)**, labelled in the UI | none |
| `/api/operations/zonal-statistics`, `/clip`, `/api/downloads/{id}` | analysis, clip fallback | custom API (not OGC Processes) | none |
| `/auth/*`, `/admin/*`, `/api/exports*`, optional `/api/catalog/datasets` | accounts, admin, exports, provenance | Agent 5 custom API | **provisional pin** `docs/openapi.agent5.provisional.json`; see `CHANGE_REQUEST_FOR_AGENT5.md` |

**Integration switch.** `demo-client/config.json → applicationApi.enabled` is `false` on this branch (the
frozen base has no such routes). **Agent 7: set it to `true` after merging Agent 5 and the gateway
routes.** With `false`, the Phase 2 checks run exactly as before and no `/auth` request is made.

## 5. National viewer performance and bounded behaviour

- Boundaries: requests always carry `bbox`; 500 features per page through `rel=next`; at most 5,000
  features per view; on overflow, zero features are drawn and the UI says "zoom in". Measured on the
  mock (12,000 district cells): national view → **1** district request, 0 features drawn; a 10°×9°
  regional view → every matching feature (e.g. 1,344 for bbox 74,17,84,26) over 3 bbox-scoped pages,
  equal to the server's `numberMatched`; one 500-feature page ≈ 130 kB, and the test caps any items
  payload at 600 kB.
- Rasters: tiles only, nothing raster-sized in the browser; year switches reuse the source (`setTiles`).
  Superseded metadata requests are discarded by generation counters, and boundary loads are aborted
  with `AbortController`.
- Shared range: one `/statistics` call per dataset, cached. TiTiler computes statistics from overviews,
  so cost does not scale with the 1.2·10⁹-cell national grids. This is untested on real COGs (**NOT_RUN**).
- Peak RSS / elapsed time on the 16 GB Docker target: **NOT_RUN** (no Docker here). The viewer is a static
  nginx site; memory belongs to the services it calls.

## 6. Responsive and accessibility checks (automated)

1366×768 with no horizontal scroll and no overlay collisions; 375 px stacked layout; every visible control
has an accessible name (automated sweep across all tabs); skip link; arrow-key tab switching; visible focus
ring; keyboard-operable year slider and swipe divider; status uses icon + word (never colour alone);
`prefers-reduced-motion` honoured; dialogs use `<dialog>`; errors are announced in live regions.

## 7. Scientific and design decisions (no human approval needed; none changes data)

- Shared display range = `[min(0, min), nice(max p98)]` over layers with the **same units and support**.
  It is display only, legend says values above are clipped. Level 1 and Level 2 never share a scale.
- Level 1 vs Level 2 is compared only as **totals over the same polygon** (pixel-centre rule, each on its
  native grid). Raw cell values are flagged as not comparable, with the cell-area ratio shown.
- A pixel B − A is shown only for the same units and grid. AOI differences are labelled "difference of
  server totals".
- The initial camera is the India study area. It falls back to the data extent only if nothing published
  is national (Phase 2 fixtures), so the legacy regression keeps working with no hard-coded city.

## 8. Blockers, risks, limitations

1. **Agent 5 API unpublished (BLOCKED for real U3).** The UI was built against the provisional contract. Any
   shape difference is confined to `js/auth.js`, `js/admin.js` and `js/exports.js`.
2. **Provenance not exposed publicly.** License and Level 2 `group_id` show "not exposed" for real data, and
   Level 2 falls back to grouping by year. Fix: catalog metadata endpoint (A5) or OGC fields (A4).
3. **OGC Tiles ranges are per dataset** until A4 configures a shared `render.rescale` (P3). The viewer
   then offers the vendor TiTiler shared range, clearly labelled.
4. **Search** is exact-match on the server unless CQL2 `LIKE` is declared (A4 request #4).
5. **Raster difference maps** need server-side band math (TiTiler expressions are disabled by design). The
   viewer shows pixel and AOI differences only. Not requested from A4/A5 as a must-have.
6. **External dependency:** MapLibre from jsDelivr (pinned and SRI), unchanged from Phase 2.
7. No Docker on this machine, so nothing here was run against a live stack or real rasters.

## 9. Coordination requests

- `CHANGE_REQUEST_FOR_AGENT5.md`: confirm `/auth`, `/admin`, `/api/exports` shapes; anonymous `/auth/me`
  200; `capabilities`; cancel link; public catalog metadata; upload size limit.
- `docs/change-requests/agent6-to-agent4.md`: shared OGC rescale; gateway routes and body limits for
  Agent 5; provenance in collections; CQL2 `LIKE`; keep `numberMatched`/`next`; TiTiler allow-list.

## 10. Screenshots (`docs/evidence/screenshots/`, regenerated by the browser suite)

- `legacy-01-phase2-fixtures.png`
- `legacy-02-after-clip-vendor.png`
- `u1-01-india-level1-2001.png`
- `u1-02-year-2008.png`
- `u1-03-level2-overlay-markers.png`
- `u1-04-level2-pune-over-level1.png`
- `u1-05-metadata.png`
- `u1-06-districts-paginated.png`
- `u1-07-search-jaipur.png`
- `u2-01-shared-scale-2011.png`
- `u2-02-swipe-2001-2011.png`
- `u2-03-side-by-side-l1-l2.png`
- `u2-04-l1-vs-l2-totals.png`
- `u2-05-zonal-drawn.png`
- `u2-06-series-2001-2011.png`
- `u2-07-aoi-too-large.png`
- `u3-01-admin-catalog.png`
- `u3-02-admin-validated.png`
- `u3-03-admin-published.png`
- `u3-04-published-layer-on-map.png`
- `u3-05-user-403.png`
- `u3-06-session-expired.png`
- `u3-07-export-full-ready.png`
- `u3-08-export-clip-3857.png`
- `usability-375px.png`
