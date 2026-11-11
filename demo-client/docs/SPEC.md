# Agent 6 — viewer completion spec

Source of truth: the Agent 6 package (`SYSTEM_PROMPT.md`, `AGENT_HANDOFF.md`, `shared/*.md`, flowchart
page 2: *layer-wise visualisation · RBAC admin posts / user gets · download dataset, CRS, geo-format*).
Gates owned: **U1, U2, U3** (`shared/ACCEPTANCE_MATRIX.md`). Frozen base `c4dac59`.

## Constraints that shape the design

| Constraint | Source | Consequence |
|---|---|---|
| Extend the existing MapLibre viewer, no framework swap | AGENT_HANDOFF §mission | Vanilla ES modules, MapLibre 4.7.1 (SRI), no build step; nginx serves the folder unchanged |
| Preserve `/viewer/` behaviour and Agent 7's browser/HTTP checks | AGENT_HANDOFF §1, `tests/e2e/test_60_viewer.py`, `tests/e2e/browser/check_viewer.py` | Keep ids `#raster #vectors #aoi-label #zonal #clip #pixel #output`, `input[name=source]`, `data-testid=download-link`, global `map`, source id `raster`, the route strings asserted in `app.js` |
| No hard-coded dataset ids, no Hyderabad camera | SHARED_CONTRACTS §4 | Everything from discovery; initial camera = India study area, data-driven zoom only when nothing national is published |
| No scientific computation in JS | AGENT_HANDOFF §mission | Totals/means/counts only from `/api/operations/zonal-statistics`; UI may subtract two *server* totals and labels it as such; no raster differencing or interpolation in the browser |
| Paginated, bbox-scoped boundaries | AGENT_HANDOFF §3, U1 | Follow `rel=next` with `bbox`, budget per view, explicit "zoom in" state, AbortController per view change |
| Agent 5 API not yet published | GIT_BRANCH §coordination, MASTER C2 | Provisional OpenAPI fixture + isolated client module + mock gateway; `config.json` switch; `CHANGE_REQUEST_FOR_AGENT5.md` |
| OGC collections do not expose provenance / license / group | ogc-services `publish.py` | Merge OGC + vendor `/tiles/datasets`; optional metadata endpoint (change request); honest "not exposed" state |

## Feature → implementation → test

| # | Requirement | Implementation | Tests |
|---|---|---|---|
| 1 | Viewer foundation, regressions | `app.js` entry keeps legacy contract; modules under `js/` | `e2e/legacy.spec.js` (ports `check_viewer.py`), `unit/legacy-contract.test.js` (ports `test_60_viewer.py` asserts) |
| 2 | Dataset browser, grouping, metadata | `js/catalog.js` normalise + group (level, year, region, kind, `provenance.group_id`); `js/layers-panel.js` | `unit/catalog.test.js`; `e2e/national.spec.js` (11 years, L2 group) |
| 3 | India map, search, paginated boundaries, loading/error/empty | `js/boundaries.js`, `js/search.js`, map status bar | `unit/boundaries.test.js` (pager, budget, abort); `e2e/boundaries.spec.js` (12k features, no limit=1000 single fetch, no full-country download) |
| 4 | Year slider, swipe/side-by-side, shared legend, Δ, L1-vs-L2, NoData, provenance badge | `js/timeline.js`, `js/compare.js`, `js/legend.js`, `js/inspector.js` | `unit/legend.test.js`; `e2e/compare.spec.js` |
| 5 | AOI from boundary / drawn / view; zonal, series, clip; progress and errors | `js/draw.js`, `js/analysis.js` | `e2e/analysis.spec.js`, `e2e/legacy.spec.js` |
| 6 | Login/logout/me, admin upload→validate→publish→status/versions, user read-only, 401/403/expiry | `js/auth.js`, `js/admin.js` against `fixtures/openapi.agent5.provisional.json` | `e2e/auth-admin.spec.js` |
| 7 | Full/clip export, dataset/version, AOI, CRS, format, progress/cancel, size/provenance | `js/exports.js` (`/api/exports`), fallback to existing `/api/operations/clip` | `unit/exports.test.js`; `e2e/exports.spec.js` |
| 8 | 1366×768, keyboard, labels, legend, disabled invalid actions, filter, deterministic state, no console errors, no big GeoJSON | `style.css` tokens/layout, `js/state.js` hash state | `e2e/usability.spec.js` (viewports, keyboard, axe-style label check, console/requests) |

## Visual design (tokens in `style.css`)

A cartographic research tool: neutral paper/ink chrome so the viridis raster carries the colour.
One accent (`--accent #1f6feb`, the existing viewer accent) for interactive state only. Comparison
identities A = accent blue, B = `#c4520b` (validated: CVD ΔE 30.7, contrast ≥ 3:1) and always
labelled "A"/"B" (never colour-only). Provenance uses status colours with icon + text. Boundaries are
ink lines with a white halo, not a competing hue. Type: system UI stack with tabular numerals; mono
for ids. Sizes 11/12/13/14/16/20/28.

## Out of scope / delegated

- Raster difference maps (needs server-side band math; TiTiler expressions are disabled) → request to A4/A5.
- Server-side shared colour range for OGC Tiles (A4, P3); viewer uses vendor TiTiler `rescale` meanwhile, labelled.
- Gateway routes for `/auth`, `/admin`, `/api/exports` (A4/A5).
