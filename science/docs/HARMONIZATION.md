# Source harmonisation decisions (Agent 01)

## Scientific grid (decision, aligned with Agent 2's change request)
**Native WorldPop-IND grid: EPSG:4326, 3 arc-second, 35075 × 34497, origin (68.185416408°E, 35.501250059°N).**
Counts stay on it untouched. GHSL is taken in its JRC-provided **`4326_3ss`** product, which lies on the *same pixel
lattice* (verified: tile R7_C26 origin is exactly 2168 columns / 7682 rows from the WorldPop origin; the tile index is
unit-tested against that). Therefore **no resampling of any kind is needed** for built-up surface: harmonisation is an
aligned window copy (`extract_aligned_window`), byte-exact, with out-of-coverage cells set to NoData.

| Quantity | Treatment | Why |
|---|---|---|
| WorldPop `people_per_cell` | untouched; windowed crop only | counts are summed, never averaged |
| GHSL total / NRES `m2_per_cell` (4326_3ss, uint16) | aligned window copy, no resampling | same lattice; values bounded by cell ground area (~8,600–9,300 m²; sample max 8,188) |
| GHSL-POP `people_per_cell` (4326_3ss) | evaluation reference, delivered unchanged | Agent 2 aggregates it to a common support |
| Bhuvan LULC (categorical vector) | rasterise by class / nearest neighbour only | class codes are never interpolated; no new information below 1:50,000 |

**NoData.** WorldPop declares −99999 (kept). The GHSL 4326_3ss tiles declare **no** NoData and use 0 for "no built-up
area". A delivery must still declare one for the importer, so the sentinel is the dtype maximum (**65535**), which
physical values (≤ cell area) cannot reach; `check_units` enforces that bound. 0 is never used as NoData.

**WorldPop 2010 is one column wider** (35076 vs 35075, same lattice; reported by Agent 2). `worldpop_grid_report` computes each
year's whole-pixel offset vs 2011 and rejects any year that is *not* on the lattice. Counts are never shifted
fractionally. Nothing here re-registers the files; Agent 2's pipeline does the whole-pixel registration.

**Fallback (not used by default): Mollweide 100 m (ESRI:54009).** If only the Mollweide product were available,
`warp_area_quantity` converts m²/cell to area fraction, averages, and multiplies by the geodesic target cell area. On the
Hyderabad window that approach changed total area by −0.57 % (edge effects only; per-cell values are not conserved because cell areas differ).
`ESRI:54009` has no EPSG code, so the repository importer would reject it; 4326_3ss avoids the issue entirely.

**Epochs.** Required by the flowchart: GHSL 2000/2005/2010. **Additional 2015** (total, NRES, POP) is registered because
Agent 2's D-L1-01 option C was approved (recorded in their `METHODOLOGY_DECISION.md`, 2026-11-07; the approval is
the project owner's chat reply and Agent 7 may want to confirm it). 2015 is used only for the 2011 weights.

**Boundaries.** geoBoundaries ADM1 (36 units) / ADM2 (735 units), commit 9469f09, are fetched and integrity-checked
(valid, unique IDs) but are **diagnostic only**: they carry no Census 2011 codes and do not satisfy the Census join.

**Count vs density.** `check_units` rejects km2/density/m3 labels, negative values and m²-per-cell values above the cell
area. Values alone cannot prove a count is not a density; the registry unit and the source's semantics are authoritative.
