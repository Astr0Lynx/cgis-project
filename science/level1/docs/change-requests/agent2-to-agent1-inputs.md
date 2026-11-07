# Change request: Agent 2 → Agent 1 (input sources and grid semantics)

**Status:** open. **Requested by:** Agent 2 (`completion/agent2-level1`). **Owner:** Agent 1 (`science/data/`, `science/inputs/`).

## Context

On the frozen base `c4dac594…` no Agent 1 delivery or manifest existed, so Agent 2 acquired the open,
CC BY 4.0 Level 1 inputs as a **documented lawful fallback** (`science/level1/scripts/fetch_inputs.sh`).
Exact files, sizes and SHA-256 are in `science/level1/provenance/INPUTS.md` and `inputs_inventory.json`.
Agent 2 did not edit any Agent 1 path.

## Requests

1. **Adopt or replace the fallback inputs** by publishing source manifests with:
   * `india-worldpop-unconstrained-YYYY` (2001–2011): WorldPop Global1 **unconstrained** `ind_ppp_YYYY.tif`
     (not the UN-adjusted or constrained variants), unchanged bytes. Agent 2 depends on the **native grid**
     (EPSG:4326, 3″, origin 68.185416408 / 35.501250059, 35075 × 34497, NoData −99999). *Do not reproject
     or resample the counts.* If Agent 1 must clip or reproject them, tell Agent 2 first, because that
     changes the parent lattice and conservation.
   * `india-ghsl-built-total-{2000,2005,2010}`, `india-ghsl-built-nres-{2000,2005,2010}`:
     GHS-BUILT-S R2023A **4326_3ss** (surface, m²/cell, uint16). Please keep the 4326_3ss product. It
     lies on WorldPop's lattice exactly (verified offset 0.000 px), so no weight regridding is needed. If
     Mollweide 100 m (54009) is delivered instead, Agent 2's area-weighted path is used. That works but
     is approximate.
   * **Additional epoch 2015** (`…-2015`, total and NRES): needed only if D-L1-01 option C is approved.
   * `india-ghsl-pop-{2000,2005,2010,2015}`: GHS-POP R2023A 4326_3ss, for evaluation.
2. **Authoritative boundaries.** Census-2011-compatible state and district polygons (stable IDs) for the
   state/district conservation and evaluation tables. These currently use geoBoundaries (diagnostic only).
3. **Checksums.** Agent 2's configs take file patterns. If Agent 1's checksums differ from
   `provenance/INPUTS.md`, Agent 2 must re-run and re-version the outputs (`output.version`).

## Compatibility

No schema change. Agent 2 reads files via `inputs.*` patterns in `config/india_full.toml`. Pointing them
at Agent 1's directory is a config-only change.
