# Methodology decisions requiring human sign-off — Level 1

Status legend: **PENDING** (requested, not approved), **APPROVED** (name, date, option recorded below).
While a decision is PENDING, every affected output is labelled `PROVISIONAL` in its catalog title and
records `post_last_epoch_policy_approval = "pending"` in provenance.

**Summary (2026-11-07): D-L1-01 APPROVED (option C), D-L1-02 APPROVED (10 × 10), D-L1-03 default kept.**

---

## D-L1-01 — Residential weights for 2011 (after the last required GHSL epoch, 2010) — **APPROVED: option C**

**Why a decision is needed.** The flowchart says "interpolate to target year", with GHSL epochs
2000/2005/2010. 2011 lies outside [2000, 2010], so no interpolation is possible from the required
epochs. The choice changes only the *within-parent* (≈1 km) distribution of 2011 population. The 2011
WorldPop totals at every ≥1 km parent are conserved under every option.

| Option | Rule for 2011 | Pros | Cons |
|---|---|---|---|
| **A — hold 2010** | `R_2011 = R_2010` | Uses only required inputs; no extrapolation risk; simplest to explain | Ignores 2010–2011 construction; 2011 weights equal 2010 weights exactly |
| **B — linear extrapolation** | `R_2011 = max(1.2·R_2010 − 0.2·R_2005, 0)` | Uses only required inputs; continues the observed 2005–2010 trend | Extrapolation amplifies epoch noise; the clip at 0 is a non-linear artefact; cells where built-up *decreased* 2005→2010 (e.g. reclassification) are pushed toward zero |
| **C — interpolate with GHSL 2015 (recommended)** | `R_2011 = 0.8·R_2010 + 0.2·R_2015` | Same rule as 2001–2010 (interpolation, not extrapolation); same release (R2023A), grid, units and licence (CC BY 4.0); bracketed by real data | Needs an **additional epoch** not in the original drawing (GHS-BUILT-S + NRES 2015), which must be explicitly authorised |

**Recommendation: C.** It keeps the method uniform across all 11 years (interpolation only), uses the same
GHSL release and processing chain, and avoids extrapolation artefacts. A remains a defensible
conservative alternative. B is not recommended.

**Impact comparison (computed on the full real India 2011 run).** All three options run in the same
tiled pass. The authoritative numbers are in `eval/policy_2011_comparison.json`, generated from the run
report `data/outputs/level1/reports/india-l1-pop-2011.run.json` (`policy_comparison_2011`), and are
summarised in `HANDOFF_REPORT.md`.

**Current state (2026-11-07).** Option C was **approved by the project owner** ("do the recommended
thing", in reply to the D-L1-01 decision request). The approval is recorded in config as
`policy_2011_approval = "approved:maitz18:2026-11-07"`, and `india-l1-pop-2011` is produced with option C,
carrying `post_last_epoch_policy_approval: "approved:maitz18:2026-11-07"` in its manifest provenance. It
is no longer labelled PROVISIONAL. The GHSL 2015 epoch (GHS-BUILT-S and NRES R2023A 4326_3ss) is thereby
an authorised additional input, used **only** for 2011. 2001–2010 do not use it.

**Exact human action required.** Fill in and commit the block below on branch
`completion/agent2-level1` (or tell Agent 2/Agent 7 the choice), then run:

```sh
# edit science/level1/config/india_full.toml:
#   [temporal] policy_2011 = "<hold_2010|extrapolate_linear|interpolate_2015>"
#              policy_2011_approval = "approved:<name>:<YYYY-MM-DD>"
PYTHONPATH=science/level1 python -m cgis_level1 run --config science/level1/config/india_full.toml --years 2011 --force
PYTHONPATH=science/level1 python -m cgis_level1 evaluate --config science/level1/config/india_full.toml --years 2011
```

```
Decision D-L1-01:  [ ] A hold_2010   [ ] B extrapolate_linear   [x] C interpolate_2015 (additional epoch authorised)
Approved by: maitz18 (maithilybhala1803@gmail.com)   Role: project owner / human operator   Date: 2026-11-07
```

---

## D-L1-02 — Parent support for `P_{d,x}` — **APPROVED: 10 × 10 WorldPop cells**

The output grid is the WorldPop grid, so a one-cell parent would make the formula the identity. The
implementation uses a 10 × 10-cell (30″, ~0.9 km) block lattice anchored at the WorldPop origin.

| Option | Effect |
|---|---|
| **10 × 10 block (default)** | WorldPop is kept exact at ~1 km; built-up decides placement within each km. Seam-safe, needs no boundaries. |
| 5 × 5 or 20 × 20 block | Weaker or stronger redistribution. A one-line config change (`parent.block`). |
| Administrative zones (district / sub-district) | Strongest redistribution: WorldPop's distribution is kept only as zone totals. Implemented (two-pass zone mode) but needs authoritative boundaries from Agent 1. |

Default proposed because it is the smallest support that makes the formula non-trivial without
discarding WorldPop's ~1 km information. A reviewer may prefer zones. Sign-off:

```
Decision D-L1-02:  [x] 10x10 block   [ ] other block: ___   [ ] admin zones: ________
Approved by: maitz18 (project owner, "do the recommended thing")   Date: 2026-11-07
```

## D-L1-03 — Zero-weight parents — default **keep WorldPop distribution** (informational)

Parents with no residential surface keep WorldPop's within-parent pattern (alternative: uniform). This
is a conservative choice that adds no information. Shown here for transparency. No sign-off is needed
unless a reviewer disagrees.
