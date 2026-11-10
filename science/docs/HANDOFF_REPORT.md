# Handoff report — Agent 01 (source acquisition and data integrity)

- Branch: `completion/agent1-data`. Frozen base: `c4dac59433f7aed90b8223ef6ba00cf4fef6449b` (verified equal to `origin/main` before branching).
- Implementation tip SHA: **the pushed branch tip** (a file cannot contain its own commit hash; the exact SHA is given in the delivery message and by `git rev-parse origin/completion/agent1-data`).
- Changed paths (all new, all owned): `science/data/**`, `science/inputs/**`, `science/docs/**`. No change to `ingestion/`, `database/`, `compose.yaml`, or any other agent's path.
- Requirement IDs: **D1 PASS, D2 PASS, D4 PASS (data-preparation scope), D3 BLOCKED**. Overall: **PARTIAL (BLOCKED on D3)** — not complete.

## Inputs, licences, access state
All 30 registry sources: `science/docs/source_inventory.json` / `SOURCE_INVENTORY.md`.
- available + downloaded + checksummed: WorldPop ×11; GHSL total/NRES/POP × 2000/05/10/15 (per-tile sha256 in the inventory); geoBoundaries ADM1/ADM2 (diagnostic only).
- available + downloaded + checksummed (added 2026-11-10): Census PCA India/State/District; DataMeet Census-2011 district polygons (joined 640/640).
- **missing:** village/town-level Census PCA (open per-district; awaits Agent 3's districts); Census-coded town/village polygons. **restricted:** Bhuvan LULC 1:50K 2011–12 (human request, `BLOCKER_REQUEST.md` B1).

## Outputs
Sample delivery (git-ignored, regenerable): `data/outputs/hyderabad-region/`. Committed examples: `science/inputs/examples/` (manifests, sample report, import logs, Agent 2 checksum cross-check). National integrity: `science/inputs/national_validation.json`; real Census↔polygon join: `science/inputs/census_district_join.json`.

## Tests
`cd science/data && PYTHONPATH=. python -m pytest -q` → **54 passed, 0 failed, 0 skipped** (synthetic fixtures). Real-input evidence: national integrity run; Hyderabad sample through the original `ingestion validate` and **`import` into a disposable PostGIS (7 rasters + 1 vector, idempotent re-run)**; byte-level cross-check with Agent 2 (11/11 WorldPop, 168/168 comparable GHSL tiles).

## Resources
Sample build 274 MB / ~8 s; import 167 MB / 12.5 s; national integrity 950 MB / 650 s; disk 24 GB incoming. Detail: `DATA_READINESS_REPORT.md`.

## Cross-agent
- A2: followed their change request — GHSL `4326_3ss` on WorldPop's lattice, 2015 epoch added, GHS-POP provided; WorldPop untouched. A2's own fallback downloads are byte-identical to mine. Their request for Census-coded boundaries remains **open (D3)**.
- A3: `CANDIDATE_TOWNS_REQUEST.md` + template written; no reply exists. Real Census/LULC samples are BLOCKED.
- A4: no ingestion change request needed (4326_3ss avoids the unsupported-CRS issue; the importer accepted all samples). Note for A4/A7: GHSL uint16 deliveries declare NoData 65535 by design (source declares none).
- Frozen-contract observation for Agent 7: the flowchart says "10 Level1 rasters" / "Bhuvan LULC 23.5 m"; the frozen context says 11 and the portal offers 1:50K vectors (`BLOCKER_REQUEST.md` B5).

## Scientific decisions / approvals
None made by Agent 1. D-L1-01 (2015 epoch) is Agent 2's, approved by the project owner per their record.

## Pending human actions
Bhuvan request (B1; cannot be done by an agent), choice/supply of town/village Census-coded polygons (B3), flowchart questions (B5). Village/town PCA (B2) needs only Agent 3's district list.

## Operator decision (2026-11-10)
The project owner chose **not** to submit the Bhuvan LULC request and asked for Agent 1's work to be pushed as delivered. Agent 1's deliverable is therefore
final for everything obtainable without that approval. Level 2 is unaffected only if Agent 7/the owner approves a documented alternative (B1).

## Completion status: **PARTIAL (BLOCKED on D3: no Bhuvan LULC, no village/town Census data or polygons)**
Agent 1 has delivered everything in its task list that did not depend on those inputs. It does not claim D3 PASS.
