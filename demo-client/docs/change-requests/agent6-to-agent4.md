# Change request: Agent 6 → Agent 4 (gateway, OGC publication, tiles)

**Status:** open. **From:** Agent 6. **To:** Agent 4 (`infra/`, `ogc-services/`, `compose.yaml`).
Agent 6 has not edited any Agent 4 path. None of these items blocks the viewer, which degrades
gracefully, but each one closes a visible gap.

| # | Request | Why | Viewer behaviour until then |
|---|---|---|---|
| 1 | **Shared colour range for comparable layers** (P3): set `ogc_publication.render.rescale` for every Level 1 year (e.g. one range for all `india-l1-pop-*`), so that the OGC tileset `style` reads *"configured shared range"* | OGC API – Tiles then portrays every year identically, and the viewer can stay on the standard route for comparisons | The legend warns "each year uses its own range". One click switches to TiTiler `rescale` with a range derived from `/statistics` (labelled vendor-specific) |
| 2 | **Gateway routes** for Agent 5: `/auth/`, `/admin/` (POST allowed, with an upload body limit sized for national rasters or a chunked upload route) and `/api/exports` (to `application-api`, while `/api/operations` stays on `processing`) | U3 | `config.json → applicationApi.enabled=false`: no calls, "Accounts not enabled" |
| 3 | **Expose public provenance** (license, `group_id`, `synthetic`, resolution) on OGC collections, *or* route Agent 5's `/api/catalog/datasets` | Metadata panel, Level 2 grouping, real/synthetic badge for real data | Shows "not exposed", groups Level 2 by year |
| 4 | **CQL2 `LIKE`** (advanced comparison operators) on boundary collections, declared in `/conformance` | Prefix search for states/districts/towns | Exact `name=` match plus a search of the features already loaded in view |
| 5 | Keep `numberMatched` in Features responses and absolute `rel=next` links (already true in Phase 2) | Paginated, budgeted boundary loading | Required |
| 6 | Keep `/tiles/datasets`, `/info`, `/statistics`, `/point` in the TiTiler allow-list | Metadata fill-in, shared range, pixel readout | Required (vendor-specific, labelled) |

No change is requested to `infra/viewer/default.conf`: the new `config.json` and `js/*.js` are plain static
files. `demo-client/.dockerignore` keeps `tests/`, `docs/` and `node_modules/` out of the viewer image built
by `compose.yaml`.
