# Shared contracts — v1 (FROZEN)

These documents are the authoritative, frozen **v1** interface contracts for CyberGIS Phase 2.
They were committed once by Agent 1 on `phase2/base` and are **read-only** for Agents 2–5.

| File | Purpose |
|---|---|
| `PROJECT_CONTEXT.md` | Phase boundaries, authorized stack, security/provenance rules |
| `ARCHITECTURE.md` | Data flow, Compose service names, gateway paths, module ownership |
| `SHARED_CONTRACTS.md` | Catalog schema, fixture IDs, ingestion CLI/manifest, HTTP API surface, error model |
| `SYNTHETIC_DATA.md` | Deterministic synthetic fixture specification (NOT real population data) |
| `GIT_AND_MERGE_POLICY.md` | Branch model, isolation rules, Agent 6 integration procedure |
| `RUN_ORDER.md` | Operator runbook and gates |
| `ACCEPTANCE_MATRIX.md` | P0 acceptance criteria and evidence requirements |

## Rules

- Do **not** edit files in this directory on a feature branch.
- To propose a change, write `docs/contract-change-requests/agentN.md` (motivation, old/new shape,
  migration impact, dependent agents, tests). Agent 6 resolves changes during integration; if a change
  must unblock parallel work, Agent 1 publishes a new baseline revision and informs all agents.

## Integrity (SHA-256, copied verbatim from the Agent 1 package manifest)

```
7f4ee8a6a4c66dfc3f618a6feaa61c2b43548f00f4c6b3180d8895b7f265e71e  ACCEPTANCE_MATRIX.md
ebcbfcbc72fb4a3f482fc20a4689271bdbaea57f0af0d7e842bb2f8f43dc0201  ARCHITECTURE.md
1441e6785d77850796154695535c77ce633b8bb3644c43f986ff8fcd46203953  GIT_AND_MERGE_POLICY.md
68d0cd0616448e93319746579a521972231423f6a3c2b2d77b3a2f8136a3a730  PROJECT_CONTEXT.md
82efff50d19f561424712559d4dcdd9ac606db21ebf252da49e333b7a6e29602  RUN_ORDER.md
40e9378d017c1b1649f13cd598ed33b9d838dc444f39580eaf88e5d4e8b44df8  SHARED_CONTRACTS.md
0656a03c2a7a207075f5a647073d62fdee877c1d30633b30d2968783a3eb0d74  SYNTHETIC_DATA.md
```

Verify with: `cd contracts && sha256sum -c <(sed -n '/^```$/,/^```$/p' README.md | grep -v '```')`
