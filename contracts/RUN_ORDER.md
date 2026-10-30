# Operator runbook: sequencing exactly six agents

## Overview

Only SIX independent agents are needed. Agents 1–5 are implementers; Agent 6 is the sole integrator and default-branch publisher. Do NOT introduce a seventh orchestrator or PR gate.

| Gate | Agents running | Condition to continue | Expected output |
|---|---|---|---|
| 0 — Repository discovery | Agent 1 only | Repo inspected; default branch & ownership confirmed | Written assumptions and `BASE_SHA` plan |
| 1 — Shared baseline | Agent 1 only | `phase2/base` pushed and SHA shared | Frozen contracts, owned module skeletons |
| 2 — Parallel build | Agents 1, 2, 3, 4, 5 | Each has its own clone/worktree and branch from identical BASE_SHA | Five pushed feature branches + handoff reports |
| 3 — Integration | Agent 6 only | Five branches complete and testable | Integrated branch and conflict resolutions |
| 4 — Final validation | Agent 6 only | Fresh-clone demo, tests, health, API conformance | Test evidence + updated docs |
| 5 — Publish | Agent 6 only | All gates pass and default branch allows fast-forward | Integrated commit on default branch, or safely reported blocker |

## How to launch

1. Open Agent 1 ZIP, provide its `SYSTEM_PROMPT.md` as the system/project instruction and `START_HERE.md` as the task. Give it authenticated GitHub access using the usual git credential mechanism, not copied tokens.
2. Tell Agent 1 to stop after **baseline checkpoint** and report `BASE_SHA` + `origin/phase2/base` before continuing its infrastructure implementation.
3. Start Agents 2–5 at that exact BASE_SHA in separate clones/worktrees. Agent 1 resumes on its own infrastructure branch concurrently.
4. Supply each agent only its assigned ZIP, repository access, `BASE_SHA`, and its own clone/worktree. No real data is required; fixture spec is in every ZIP.
5. Do not start Agent 6 for merging until all five agents return a pushed branch SHA and handoff report.
6. Give Agent 6 its ZIP plus the five branch names and SHAs, BASE_SHA, and status/limitations from all five reports. Agent 6 must fetch code from GitHub directly, not trust text reports as code.

## Cross-agent requests

- If Agent 3 requires a new DB column, it writes a request in `ingestion/docs/contract-change-request.md`; Agent 2 can address this on its branch only if coordinated and before freeze. Otherwise Agent 6 handles it during integration.
- If Agent 4 needs a new gateway route, it writes `ogc-services/docs/gateway-request.md`; Agent 1/6 wires it in. Same for Agent 5.
- If fixtures expose an inconsistency, Agent 3 owns fixture code; other agents write failing tests and report the issue, never fork fixture definitions.
- Interfaces are frozen; a blocker should be escalated with a proposed patch and migration impact, never silently bypassed.

## Minimum inputs per agent

| Agent | Must receive | Can proceed without |
|---|---|---|
| 1 Infrastructure | Repo URL, Git auth, default branch, agreed stack | Real data, other branches |
| 2 PostGIS | Repo + BASE_SHA, frozen schema, synthetic fixture spec | Docker implementation complete, real data |
| 3 Ingestion | Repo + BASE_SHA, DB catalog contract, synthetic fixture spec | Real data, OGC endpoints |
| 4 OGC services | Repo + BASE_SHA, collection contract, DB schema, sample fixture IDs | Finished ingestion service; use mock records |
| 5 Spatial ops | Repo + BASE_SHA, processing contract, fixture shape | Finished OGC implementation; use mocked catalog and raster assets |
| 6 Integration | Repo + BASE_SHA, **all five pushed branch SHAs**, reports | PRs, manual approval workflow |

## Final demonstration

`docker compose up --build`, initialization/migration, fixture generation, fixture import, dataset catalog query, vector feature API query, tile endpoint/viewer, Town A clip, zonal sum=100 for 2001, download GeoTIFF with CRS, QGIS interoperability, restart persistence.

## Human checkpoints

- Human supplies GitHub access and starts six agents as shown.
- Human only intervenes for blockers such as protected branches, missing GitHub permissions, substantial existing repository conflicts, or an architecture change beyond this contract.
- No manual PR approval is required; Agent 6 must independently prove and publish a tested integration or stop on genuine blockers.
