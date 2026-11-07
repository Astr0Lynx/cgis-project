# Level 1 method: India-wide dasymetric redistribution, 2001–2011

This file is the method of record for `science/level1` (Agent 2). It implements page 1 of the original
handwritten flowchart (`reference/CyberGIS_Project_Flow.pdf`):

```
input: WorldPop unconstrained (100 m raster)                         -> (1) P
       GHSL Total built-up      [2000, 2005, 2010]                     -> (2) T_e
       GHSL Non-residential     [2000, 2005, 2010]                     -> (3) N_e
(2) - (3) => Residential [2000, 2005, 2010]       -> interpolate to target year -> Residential [target year]
P_{d,x} * R_{x,i} / sum_j R_{x,j}                 -> LEVEL 1 OUTPUT: 100 m raster redistributed for target year
Evaluation: correlation with GHSL-Pop
```

## 1. Inputs and their semantics

| Symbol | Product | Unit | Grid | Role |
|---|---|---|---|---|
| `P` | WorldPop Global1 unconstrained, India, `ind_ppp_YYYY.tif`, YYYY = 2001…2011 (doi:10.5258/SOTON/WP00645) | people per cell (count, *not* density) | EPSG:4326, 3″ (0.000833333333°), 35075 × 34497, NoData −99999, float32 | parent population |
| `T_e` | GHS-BUILT-S R2023A total built-up **surface**, epochs 2000/2005/2010 (+2015 only for the 2011 option C) | m² of built-up surface per cell | EPSG:4326, 3″ tiles (10° × 10°), uint16, no NoData (0 over sea) | weight input |
| `N_e` | GHS-BUILT-S **NRES** R2023A (non-residential surface), same epochs, release, tiling | m² per cell | identical to `T_e` | weight input |
| reference | GHS-POP R2023A, epochs 2000/2005/2010/2015 | people per cell | identical to `T_e` | evaluation only |

GHS-BUILT-**V** (volume) is never used; total and NRES are always from the same release (R2023A), epoch
and grid, so `T − N` is a valid surface difference.

**Grid relationship (verified on the real files).** WorldPop's origin (68.185416408°E, 35.501250059°N) and
the GHSL 4326_3ss tile origins (e.g. 69.99208307850°E, 29.09958341912°N) lie on the *same* 3″ lattice,
which is offset by half a cell from the integer-degree lattice. The code computes the sub-pixel offset
globally (`grid.source_mapping`), and for the real data it is exactly `(fx, fy) = (0, 0)`: WorldPop cell
(r, c) corresponds one-to-one with a GHSL cell, so **no resampling of either counts or weights happens**.
The cell-size difference between the files (≈4·10⁻⁹ relative) accumulates to < 2·10⁻⁴ px across India.
If a future input has a genuine sub-pixel offset, weights (never counts) are moved with an exact
area-overlap transfer (`core.shift_weights`); a different cell size or CRS uses GDAL's area-weighted
`sum` warp. Both are tested.

## 2. Target grid

The **WorldPop native grid** of each year (all 11 years are verified to share one grid; a mismatch aborts
the run). Choosing it means WorldPop counts are never resampled, every output cell maps to exactly one
input cell, and the output is a ~100 m (3″; ≈ 92 m N–S, 77–92 m E–W across India) `people_per_cell`
raster. Output: float32, NoData −99999, wherever WorldPop is NoData (sea, outside coverage); otherwise ≥ 0.

## 3. Residential surface proxy (per GHSL epoch)

`R_e = max(T_e − N_e, 0)`, computed in float64 on the native GHSL grid before any temporal step.
Cells where `N_e > T_e` (a known inconsistency between two separately modelled layers) are clipped to 0
and counted (`residential.nres_gt_total`, surplus m²) in every run report. NaN/NoData in either input
gives a *missing* weight (counted, treated as 0 in the redistribution), never a silent 0.
uint16 inputs are cast to float before subtraction (no wrap-around; tested).

## 4. Temporal rule (per target year)

Weights are linear in the epoch surfaces (the clip at 0 is per epoch, so interpolation of non-negative
values stays non-negative):

| Year | Rule |
|---|---|
| 2001–2004 | `R_t = (1−w)·R_2000 + w·R_2005`, `w = (t−2000)/5` |
| 2005 | `R_2005` exactly |
| 2006–2009 | `R_t = (1−w)·R_2005 + w·R_2010`, `w = (t−2005)/5` |
| 2010 | `R_2010` exactly |
| 2011 | **no interpolation exists** (last required epoch is 2010). Options: A hold 2010; B linear extrapolation `1.2·R_2010 − 0.2·R_2005`, clipped at 0; C interpolation `0.8·R_2010 + 0.2·R_2015` with the additional GHSL 2015 epoch of the same release. The pipeline **refuses** to produce 2011 without an explicit `temporal.policy_2011`. See `METHODOLOGY_DECISION.md` (D-L1-01): C is recommended and is used *provisionally*; outputs are labelled `PROVISIONAL` until a human approves. All three options are computed in the same pass and their impact is reported. |

Linear interpolation of a surface between five-year epochs assumes steady construction between epochs;
it is applied to the *weights only*, so the annual WorldPop totals (which carry the real annual change)
are never altered.

## 5. Redistribution: `p_ai = P_a · R_ai / Σ_j R_aj`

**Parent support `a`.** Output and WorldPop share one 100 m grid, so if the parent were a single WorldPop
cell the formula would be the identity. The flowchart's `P_{d,x}` (population of target area `x`) is
therefore implemented on a coarser parent: **10 × 10 WorldPop cells (30″, ~0.9 km)**, on a lattice
anchored at the WorldPop raster origin. `P_a` is the sum of WorldPop counts over the valid cells of the
block, and `i` ranges over the block's valid cells. This keeps WorldPop's totals exact at ~1 km while moving
population, within each km, onto the cells where GHSL observes residential surface (unconstrained WorldPop
spreads some population onto unbuilt land). The block size is a config parameter (`parent.block`), and an
administrative-zone parent mode (two-pass, `core.zone_sums` / `core.redistribute_zones`) is implemented
and tested for when authoritative boundaries are delivered. Changing the parent support is a methodological
choice; it is listed as D-L1-02 in `METHODOLOGY_DECISION.md`.

**Eligibility.** Only cells where WorldPop has data are eligible targets and contribute to `P_a`, so
population never moves into the sea, lakes or other WorldPop NoData. Every eligible cell belongs to
exactly one parent; partial parents at the raster edge are padded with *invalid* cells.

**Zero-weight fallback.** If `Σ_j R_aj = 0` (no residential surface in the parent, which is common in rural
India where GHSL detects no built-up), the parent keeps **WorldPop's own distribution** (identity). This is
the most conservative choice: it neither invents structure nor discards population. The number of such
parents and the population they hold are reported per year. (`uniform` is available as an alternative.)

**Conservation.** For every parent, `Σ_i p_ai = P_a` exactly in real arithmetic. The implementation
computes in float64, writes float32, and **re-measures** the per-parent balance on the cast values that
are written. Tolerances fixed *before* the real run: relative 1·10⁻⁵ (per parent and national) or absolute
1·10⁻³ people per parent. The national sum of the assembled COG is re-read block by block and compared
with the WorldPop input sum and the per-chunk sums. Any failure marks the year `failed` and no PASS is
reported.

## 6. Countrywide execution under a 16 GB budget

* Deterministic tiles: full-width row bands of 200 rows (WorldPop GeoTIFFs store one row per strip). Tile
  heights are multiples of the parent block, so no parent crosses a seam. Outputs are **bit-identical
  across tile sizes** (tested: 100 vs 50, square vs band).
* Bounded worker pool (`run.workers`), one year at a time. No national array is ever materialised.
  A resource monitor samples the RSS of the whole process tree and aborts if `run.max_rss_gb` is exceeded.
  Peak RSS, elapsed time and free disk space are written into every run report.
* Checkpoints: each tile writes a compressed chunk GeoTIFF and a JSON sidecar (stats + SHA-256), using
  atomic renames. A restart re-verifies each chunk's checksum, reuses good chunks, and recomputes missing
  or corrupt ones (tested). `runlog.jsonl` is the append-only restart log. A year whose COG and report
  are already present and checksum-valid is skipped (idempotent).
* Assembly: a VRT of chunks, then a GDAL COG (DEFLATE + floating-point predictor, 512 blocks, NEAREST
  overviews for display only, BigTIFF if needed). The COG is validated with `rio-cogeo` and statistics
  are re-read.

## 7. Products and catalog

`india-l1-pop-YYYY` (YYYY = 2001…2011) COGs, plus v1 ingestion manifest entries (`product_level=level1`,
`units=people_per_cell`, `checksum_sha256`, `expect` block with CRS, NoData, dtype, size, resolution and
bounds), with provenance: upstream SHA-256s for WorldPop and every GHSL tile, model commit, config SHA-256,
temporal rule, post-2010 policy and approval state, grid, mass balance, licences. The manifests pass the
repository's **unchanged** `ingestion` validator (`python -m ingestion.cli validate`), so import goes
through the Agent 4 catalog path. COGs are never committed to Git.

## 8. Evaluation against GHS-POP (`eval/`)

* **Reference.** GHS-POP R2023A on the identical 3″ lattice (no regridding). For non-epoch years it is
  a linear interpolation of the bracketing GHS-POP epochs (flagged `reference_temporal=interpolated`).
  GHS-POP is a different **model** (census disaggregated with GHSL built-up), so it is *not* ground truth.
  Agreement measures consistency, and because GHS-POP uses GHSL built-up too, it is not independent of
  our weights.
* **Common support and unit.** People per support unit, restricted to cells where Level 1 is valid.
  The reference population falling outside that mask is reported as a coverage term. Supports: native
  ~100 m cells (streaming moments, plus Spearman on a deterministic systematic 1/289 sample), ~1 km and
  ~10 km blocks (exact), and state (ADM1) and district (ADM2) zones (cell-centre rasterisation, one zone
  per cell).
* **Metrics.** n, totals, Pearson, Pearson on log1p, Spearman, MAE, RMSE, mean bias, relative total
  bias, median and 95th-percentile absolute residual, median relative residual, R² vs the reference.
  The **same metrics for raw WorldPop** are reported as a baseline. At supports that are unions of whole
  parents (1 km, 10 km), Level 1 ≡ WorldPop by construction, so the model's effect is visible only at
  100 m and across zone boundaries. This is stated in every bundle.
* **Figures.** A national 10 km residual map per year, and 100 m zooms (WorldPop | Level 1 | GHS-POP)
  for five representative areas in 2001, 2006 and 2011.
* No accuracy threshold is imposed. Weak or negative results are reported as they are.

## 9. Known limitations

* GHSL NRES and total are separately modelled, and `NRES > total` occurs (clipped and counted).
* Linear interpolation of built-up between 5-year epochs cannot capture non-linear construction timing.
  2011 depends on the D-L1-01 decision.
* In zero-weight parents (no detected residential surface), WorldPop's unconstrained distribution is
  kept as-is, so its spatial error remains there.
* The 30″ parent fixes the population at ~1 km to WorldPop. Errors in WorldPop's ~1 km totals propagate
  unchanged.
* The evaluation reference is not independent (GHS-POP also uses GHSL built-up) and is temporally
  interpolated for 9 of 11 years.
* ADM1/ADM2 zones for diagnostics come from geoBoundaries (2011 DataMeet states; 2021 LGD-based districts),
  not authoritative Census 2011 boundaries from Agent 1.
