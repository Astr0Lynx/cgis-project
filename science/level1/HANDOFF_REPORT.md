# Handoff report: Agent 02, India-wide Level 1 model and evaluation

- **Role:** Level 1 model and evaluation
- **Branch:** `completion/agent2-level1`
- **Frozen base:** `c4dac59433f7aed90b8223ef6ba00cf4fef6449b` (verified: `origin/main` equalled it at the start and again
  before this report; the branch descends from it)
- **Implementation tip SHA (the code that produced every output below):** `058889b179793d2649fd26ca5b4a10133b8071ed`.
  All 11 run reports record this commit with `dirty_worktree: false`. The final tip SHA of the branch (this report and
  evidence added on top of that commit) is sent to Agent 7 in the hand-over message and is `git rev-parse origin/completion/agent2-level1`.
- **Changed paths:** only `science/level1/**` (about 180 files, mostly small JSON evidence and PNG figures, 16 MB total; `git diff --name-only c4dac59..HEAD | grep -v '^science/level1/'` is empty).
  Ownership confirmed: no Agent 1, 3, 4, 5, 6 or 7 path was touched. No PR, no force-push, no history rewrite, `main` untouched.
- **Requirement IDs addressed:** S1, S2, S3 (A2). Status in the table at the end.
- **Completion status:** **PASS for Agent 2's scope (S1, S2, S3)**, with the limitations in section 12. Catalog registration was evidenced against a disposable PostGIS, not the project's Compose stack (Agent 7 repeats that in integration).

## 1. What was built

`science/level1/` implements the original page-1 flowchart for India 2001–2011:
`R_e = max(GHS-BUILT-S total − NRES, 0)` → temporal interpolation of the GHSL epochs → `p_ai = P_a·R_ai/Σ_j R_aj` on
10 × 10-cell WorldPop parents, with a documented zero-weight fallback, bounded-memory tiled execution, checkpoints and
restart, lossless COG output, v1 ingestion manifests with full provenance, and a GHS-POP evaluation. See `METHOD.md`
(method of record), `README.md` (commands), `METHODOLOGY_DECISION.md` (human decisions), `eval/README.md` (results and reading guide).

## 2. Real inputs versus synthetic fixtures (kept strictly separate)

| | Real | Synthetic |
|---|---|---|
| Production | **11 national runs, 2001–2011, every year, full India extent** (`extent_is_full_worldpop_grid: true`, 393,921,894 valid cells; 395,036,259 in 2010) | `synthetic.toml` (also 11 years, a 230 × 245-cell fixture) |
| Evaluation | 11 national bundles vs GHS-POP R2023A | `eval/synthetic/` (labelled SYNTHETIC; proves software only) |
| Samples | Delhi NCR real sample, 11 years, bit-identical to the national product at the same pixels | n/a |
| Unit tests | 47 (all synthetic or tiny golden rasters); passed locally and in the 16 GB-limited container | |

Synthetic results are never presented as science. The synthetic manifest title says SYNTHETIC and `examples/manifest.synthetic-level1.json` is
the stable synthetic manifest for Agents 3, 4 and 6.

## 3. Input datasets, versions, licences and access state

All open, no access controls (details, citations and URLs in `provenance/INPUTS.md`; per-file SHA-256 in
`provenance/inputs_inventory.india-full.json`: 181 files, 25.08 GB, none missing):

* WorldPop Global1 **unconstrained** `ind_ppp_2001…2011.tif` (CC BY 4.0, doi:10.5258/SOTON/WP00645), 11 × ~1.84 GB.
* GHS-BUILT-S R2023A total and NRES, `4326_3ss`, epochs 2000/2005/2010 (+2015 only for the approved 2011 option C), CC BY 4.0, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA.
* GHS-POP R2023A `4326_3ss`, epochs 2000/2005/2010/2015 (evaluation reference only), CC BY 4.0, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE.
* geoBoundaries gbOpen IND ADM1 (36) and ADM2 (735), commit 9469f09, diagnostic evaluation zones only.

**Fallback acquisition.** Agent 1 deliveries did not exist on the frozen base, so the open inputs were fetched with
`scripts/fetch_inputs.sh` (documented lawful fallback; WorldPop FTP mirror because the HTTPS server is throttled to ~0.1 MB/s per connection). Requests to Agent 1 are in `docs/change-requests/agent2-to-agent1-inputs.md`.

## 4. Produced outputs

COGs live in git-ignored `data/outputs/level1/cog/` (13.18 GB total, **not committed**). Manifest: `data/outputs/level1/manifest.json`
(copy: `eval/india-full/manifest.json`). Grid for all 11: EPSG:4326, 35075 × 34497, transform
`(0.0008333333300071276, 0, 68.185416408, 0, −0.0008333333299997101, 35.501250059)`, float32, NoData −99999,
units `people_per_cell`, DEFLATE + predictor 3, 512 blocks, overviews 2–128 (NEAREST, display only), `product_level=level1`.

### Outputs

| Year | dataset_id | status | SHA-256 (COG) | size GB | people (sum) | rel. mass err | max parent rel. err | full grid |
|---|---|---|---|---|---|---|---|---|
| 2001 | `india-l1-pop-2001` | ok | `1211452f7e78a2f9f20cd7ed18975dd3fe0f21f573028db787ca06d6f7cfaa65` | 1.17 | 1,029,061,968 | 5.4e-12 | 6.0e-08 | True |
| 2002 | `india-l1-pop-2002` | ok | `12a9c05d90fbe96584e51260e1122897672e3adb8481b063ceebee1fc040295c` | 1.17 | 1,045,671,837 | 7.4e-12 | 6.0e-08 | True |
| 2003 | `india-l1-pop-2003` | ok | `02dcb1836268639f3d5885f87f1d9a05e5889878c38e70e94e1a9b3de0d28d3f` | 1.17 | 1,062,610,097 | 2.1e-11 | 6.0e-08 | True |
| 2004 | `india-l1-pop-2004` | ok | `06d3122d05ffb0dd3af71948f5ac8887a349fa4dcc9d73ed143a3404d06c2283` | 1.17 | 1,079,884,030 | 1.4e-11 | 6.0e-08 | True |
| 2005 | `india-l1-pop-2005` | ok | `d76cc9db6562f6cf80704155a9b74637617ad0feeaf97b04bb9bfac240708665` | 1.17 | 1,097,501,116 | 1.6e-12 | 6.0e-08 | True |
| 2006 | `india-l1-pop-2006` | ok | `a03beac1f17bd34c8f0d9fc084a6efa168e4c27e8b27ffe17773eda368fc44ba` | 1.22 | 1,115,469,040 | 1.3e-12 | 6.0e-08 | True |
| 2007 | `india-l1-pop-2007` | ok | `ef6d2f38f29c4abd9d686cafa8da4bea87b583291061004768dfd736cce79c48` | 1.21 | 1,133,795,691 | 5.6e-12 | 6.0e-08 | True |
| 2008 | `india-l1-pop-2008` | ok | `bfab6f399b059b1c58aebfea75a82cd00c7fe680b83fe307945daba6c43ac39e` | 1.21 | 1,152,489,180 | 1.2e-11 | 5.9e-08 | True |
| 2009 | `india-l1-pop-2009` | ok | `583151d0dfb1808f905eed79fd4fd9e03878d71ffca035969083e178106763a9` | 1.21 | 1,171,557,817 | 7.4e-12 | 6.0e-08 | True |
| 2010 | `india-l1-pop-2010` | ok | `38914c269e3363cdeae92b12141ac9bf1a6dcc7debdb1597ebafd1c2c8922131` | 1.21 | 1,191,010,159 | 4.6e-12 | 6.0e-08 | True |
| 2011 | `india-l1-pop-2011` | ok | `8284c2877c49d7329ea6b6775448a265c2403722f24466bb1ae218bdd34d3488` | 1.26 | 1,210,854,977 | 1.4e-12 | 6.0e-08 | True |

### Resources (16 GB target; budget 12 GiB for the process tree)

| Year | compute s | COG assembly s | eval s | peak RSS GiB (run) | peak RSS GiB (eval) | workers | min free disk GiB |
|---|---|---|---|---|---|---|---|
| 2001 | 174.9 | 154.6 | 121.2 | 3.42 | 5.45 | 4 | 35.5 |
| 2002 | 173.8 | 151.5 | 169.3 | 3.84 | 6.07 | 4 | 35.5 |
| 2003 | 171.5 | 156.1 | 166.3 | 3.86 | 6.53 | 4 | 35.5 |
| 2004 | 174.8 | 151.8 | 120.2 | 3.38 | 6.07 | 4 | 35.5 |
| 2005 | 145.6 | 155.8 | 108.6 | 3.42 | 6.11 | 4 | 35.5 |
| 2006 | 174.5 | 158.1 | 121.7 | 3.54 | 6.52 | 4 | 35.5 |
| 2007 | 177.0 | 126.5 | 121.0 | 3.79 | 6.15 | 4 | 35.5 |
| 2008 | 124.5 | 109.1 | 121.8 | 3.89 | 6.15 | 4 | 35.5 |
| 2009 | 121.2 | 109.8 | 122.6 | 3.75 | 6.55 | 4 | 35.5 |
| 2010 | 100.5 | 111.5 | 109.2 | 3.76 | 6.02 | 4 | 35.5 |
| 2011 | 359.4 | 122.9 | 122.1 | 4.27 | 5.68 | 4 | 35.6 |

Totals: compute 31.6 min, assembly 25.1 min, evaluation 23.4 min, max peak RSS 6.55 GiB, COGs 13.18 GB.

### Redistribution diagnostics

| Year | parents | zero-weight parents | % parents | population in zero-weight parents | % population |
|---|---|---|---|---|---|
| 2001 | 4,017,858 | 1,210,209 | 30.1 | 97,874,978 | 9.5 |
| 2002 | 4,017,858 | 1,210,209 | 30.1 | 99,350,126 | 9.5 |
| 2003 | 4,017,858 | 1,210,209 | 30.1 | 100,734,592 | 9.5 |
| 2004 | 4,017,858 | 1,210,209 | 30.1 | 104,291,005 | 9.7 |
| 2005 | 4,017,858 | 1,210,211 | 30.1 | 103,208,698 | 9.4 |
| 2006 | 4,017,858 | 1,199,633 | 29.9 | 103,848,767 | 9.3 |
| 2007 | 4,017,858 | 1,199,633 | 29.9 | 106,407,617 | 9.4 |
| 2008 | 4,017,858 | 1,199,633 | 29.9 | 105,003,827 | 9.1 |
| 2009 | 4,017,858 | 1,199,633 | 29.9 | 105,111,572 | 9.0 |
| 2010 | 4,053,138 | 1,224,351 | 30.2 | 104,867,228 | 8.8 |
| 2011 | 4,017,858 | 1,186,872 | 29.5 | 99,699,129 | 8.2 |

### Evaluation vs GHS-POP (Level 1 | WorldPop baseline)

| Year | ref | 100 m Pearson | 100 m Spearman* | 100 m MAE | 100 m RMSE | 1 km Pearson | 10 km Pearson | district Pearson | district Spearman | rel. total bias |
|---|---|---|---|---|---|---|---|---|---|---|
| 2001 | interpolated | 0.518 \| 0.547 | 0.585 \| 0.319 | 2.708 \| 3.867 | 18.496 \| 17.946 | 0.738 | 0.953 | 0.999 | 0.999 | -0.038 |
| 2002 | interpolated | 0.520 \| 0.550 | 0.590 \| 0.320 | 2.738 \| 3.911 | 18.571 \| 18.013 | 0.739 | 0.954 | 0.999 | 0.999 | -0.040 |
| 2003 | interpolated | 0.523 \| 0.554 | 0.593 \| 0.321 | 2.769 \| 3.960 | 18.655 \| 18.097 | 0.741 | 0.954 | 0.999 | 0.999 | -0.041 |
| 2004 | interpolated | 0.504 \| 0.538 | 0.594 \| 0.316 | 2.854 \| 4.050 | 19.204 \| 18.394 | 0.727 | 0.953 | 0.999 | 0.999 | -0.042 |
| 2005 | exact_epoch | 0.525 \| 0.560 | 0.599 \| 0.321 | 2.839 \| 4.065 | 18.964 \| 18.343 | 0.745 | 0.953 | 0.999 | 0.999 | -0.042 |
| 2006 | interpolated | 0.521 \| 0.555 | 0.600 \| 0.340 | 2.882 \| 4.120 | 19.099 \| 18.422 | 0.737 | 0.953 | 0.999 | 0.999 | -0.041 |
| 2007 | interpolated | 0.512 \| 0.548 | 0.604 \| 0.335 | 2.950 \| 4.196 | 19.387 \| 18.539 | 0.729 | 0.952 | 0.999 | 0.999 | -0.039 |
| 2008 | interpolated | 0.535 \| 0.570 | 0.608 \| 0.345 | 2.905 \| 4.190 | 19.047 \| 18.386 | 0.748 | 0.953 | 0.999 | 0.999 | -0.037 |
| 2009 | interpolated | 0.540 \| 0.576 | 0.611 \| 0.349 | 2.925 \| 4.233 | 19.072 \| 18.424 | 0.753 | 0.953 | 0.999 | 0.999 | -0.035 |
| 2010 | exact_epoch | 0.548 \| 0.583 | 0.615 \| 0.352 | 2.916 \| 4.247 | 19.057 \| 18.449 | 0.759 | 0.952 | 0.999 | 0.999 | -0.033 |
| 2011 | interpolated | 0.561 \| 0.590 | 0.628 \| 0.386 | 2.902 \| 4.254 | 18.818 \| 18.320 | 0.764 | 0.953 | 0.999 | 0.999 | -0.029 |

\* Spearman at 100 m on the deterministic systematic 1/289 sample. At 1 km and 10 km Level 1 equals WorldPop by construction, so only the Level 1 value is shown.

### Coverage inside India (ADM1 union)

| Year | GHS-POP inside India | ... outside Level 1 mask | fraction | Level 1 outside all ADM1 |
|---|---|---|---|---|
| 2001 | 1,081,646,454 | 12,073,577 | 0.0112 | 1,082,270 |
| 2002 | 1,100,704,083 | 12,274,674 | 0.0112 | 1,091,765 |
| 2003 | 1,119,761,711 | 12,475,772 | 0.0111 | 1,107,268 |
| 2004 | 1,138,819,340 | 12,676,869 | 0.0111 | 1,155,401 |
| 2005 | 1,157,876,969 | 12,877,967 | 0.0111 | 1,135,355 |
| 2006 | 1,175,122,487 | 13,056,616 | 0.0111 | 1,150,616 |
| 2007 | 1,192,368,005 | 13,235,265 | 0.0111 | 1,179,919 |
| 2008 | 1,209,613,523 | 13,413,914 | 0.0111 | 1,147,740 |
| 2009 | 1,226,859,041 | 13,592,563 | 0.0111 | 1,174,521 |
| 2010 | 1,244,104,560 | 13,766,276 | 0.0111 | 1,196,651 |
| 2011 | 1,260,584,407 | 13,933,953 | 0.0111 | 1,160,623 |


Mass balance per year: relative error ≤ 2.1·10⁻¹¹ nationally, ≤ 6.0·10⁻⁸ per parent (tolerance fixed in advance: 10⁻⁵ relative or 10⁻³ people absolute). Zero negative values, zero NaN/Inf, valid-cell count equal to WorldPop's in every year, `rio-cogeo` validation `is_cog: true`.

## 5. Independent verification

* **Spot-check** (`scripts/spot_check.py`): 40 random populated parents per year (440 parents, 44,000 cells), recomputed with a naive loop directly from the raw WorldPop and GHSL tile files (no VRT, no `core`
  code): max relative cell difference ≤ 5.8·10⁻⁸ and parent error ≤ 4.4·10⁻⁸ in all 11 years. Results in `data/outputs/level1/spot_checks/` (copied to `eval/india-full/spot_checks/`).
* **Seams:** outputs are byte-identical across tile sizes 100 vs 50 and band vs square tiling (test `test_seam_free_independent_of_tile_size`); the Delhi sample (different tiling and extent) equals the national product pixel for pixel in all 11 years (`eval/sample-delhi/sample_vs_national.json`).
* **Determinism:** forced re-runs reproduce identical SHA-256 (2003: same checksum before and after code changes that did not touch the maths); checkpoint corruption is detected and recomputed (test).
* **Repository validator:** `python -m ingestion.cli validate --manifest data/outputs/level1/manifest.json` → 11 of 11 `validated` (every block read, `expect` block, checksum). Log: `eval/india-full/ingestion-validate.jsonl`.

* **Catalog import (real, unchanged Agent 4 migrations and importer):** all 11 datasets imported into a disposable PostGIS
  (`postgis/postgis:17-3.5@sha256:01a6a70e…`, the repo's pinned image) from the `cgis-level1` Linux container (`--memory=16g`,
  kernel 6.6 WSL2): **11 of 11 `imported`, 0 failed, import exit 0** (about 40 min, 06:31-07:11 UTC).
  A second import of the same manifest returned **11 of 11 `unchanged`** (idempotent, nothing duplicated or overwritten).
  `catalog.datasets` then holds 11 rows, all `kind=raster, product_level=level1, units=people_per_cell, crs=EPSG:4326,
  nodata=-99999.0, status=ready`, 11 distinct checksums, **every checksum equals the manifest value and the SHA-256 in the table above**,
  `provenance.synthetic = false` for all, and the 2011 row carries `post_last_epoch_policy_approval = approved:maitz18:2026-11-07`
  with `model_commit 058889b…`. Valid-cell counts and population sums in the importer output equal the run reports
  (e.g. 2011: 393,921,894 cells, 1,210,854,976.70 people; 2010: 395,036,259 cells). Evidence: `eval/india-full/catalog/`
  (`catalog-import.jsonl`, `catalog-import-rerun.jsonl`, `catalog-rows.txt`, `catalog-status.txt`, `catalog-migrate.log`).
  The importer wrote 13.07 GB of assets (about the same as the 13.18 GB inputs, so plan the assets volume accordingly).
  The container's memory counter peaked at 9.4 GB after the import and 10.7 GB after the re-import; that counter includes filesystem
  page cache from reading and writing 13 GB, so it is **not** the process RSS, but it stayed under the 16 GB cap.
* **Attempts that failed before this, for the record:** several native-Windows attempts failed (first a stale host `proj.db` giving
  `unsupported_crs`; then repeatedly the importer's Windows-only `fsync` defect, section 10); an overlapping-run log race corrupted another
  attempt's log (kept as `catalog-import.attempt1-garbled.*` locally, not committed). None involved the data.


## 6. Scientific/model decisions and human approvals

* **D-L1-01 (2011 weights): APPROVED, option C** by the project owner on 2026-11-07 ("do the recommended thing"): `R_2011 = 0.8·R_2010 + 0.2·R_2015`. Recorded in config as `approved:maitz18:2026-11-07`, in `METHODOLOGY_DECISION.md`, and in the 2011 manifest provenance. The GHSL 2015 epoch is an authorised additional input used for 2011 only. The three options were all computed (`eval/india-full/policy_2011_comparison.json`): hold-2010 and linear extrapolation differ from C in 18.6 and 15.5 million cells by more than 0.5 people; against GHS-POP, C is marginally best (Pearson 0.5613 vs 0.5574 and 0.5570).
* **D-L1-02 (parent support 10 × 10 cells): APPROVED** by the same instruction. D-L1-03 (zero-weight fallback keeps WorldPop's distribution): default kept, informational.
* **Note on the approval record:** the approval was given by the user in a message answering the decision request ("do the recommended thing"). It is not a signed document; the sign-off blocks in `METHODOLOGY_DECISION.md` record it with the user's account name and date, and Agent 7 may want to confirm it.
* Policy for every year: 2001–2004 interpolate 2000→2005; 2005 and 2010 exact epochs; 2006–2009 interpolate 2005→2010; 2011 interpolate 2010→2015. Exact weights are in each run report under `temporal`.

## 7. Scientific evaluation (GHS-POP), summary

Full tables, reading guide and limitations: `eval/README.md`; per-year bundles in `eval/india-full/*.eval.json`; maps in `eval/india-full/figures/`. Headlines (all measured, no threshold):
at 100 m, Level 1 has about 30 % lower MAE and much higher Spearman (0.59–0.63 vs 0.32–0.39) than raw WorldPop against GHS-POP, but a slightly lower Pearson (about −0.03) and slightly higher RMSE; at 1 km Pearson is 0.73–0.76, at 10 km 0.95, at district level 0.999 (these last three are WorldPop-vs-GHS-POP by construction, because Level 1 is identical to WorldPop at whole-parent supports). GHS-POP is not ground truth, not independent (it uses GHSL built-up), and is interpolated for 9 of 11 years.

## 8. Resource use on the 16 GB target (measured, not estimated)

Host: Windows 11, 31.4 GB RAM, 22 logical CPUs, Docker VM 16.4 GB. Enforced budget: 12 GiB for the whole process tree (`run.max_rss_gb`), sampled every 0.25 s. 4 worker processes, tile = 200 rows × full width.
**Peak RSS: 4.27 GiB for production (max over 11 years), 6.55 GiB for evaluation.** Total elapsed: production compute 31.6 min + COG assembly 25.1 min, evaluation 23.4 min (11 years, per-year numbers above). Minimum free disk sampled during the final production runs: 35.5 GiB (earlier, while downloads and runs overlapped, the volume dipped to about 18 GB free). Disk: inputs 25.1 GB, final COGs 13.2 GB, transient chunks about 0.85 GB for the year being processed (deleted after a successful assembly), VRTs negligible. So the need beyond inputs is about 15 GB (13.2 GB COGs + chunks + one `.part` COG while assembling); the run is one year at a time and restartable. Delhi sample: 0.36 GiB peak, 1.9 min for 11 years including evaluation (`benchmarks/sample-delhi.json`).
The memory budget is exercised by the real national runs, not extrapolated from a sample. The national *production* and *evaluation* ran natively on the Windows host (not inside a 16 GB-limited container). The unchanged *importer* did run national-size data in a `--memory=16g` Linux container (section 5), and the test suite ran there too (47 passed, `benchmarks/pytest-container.txt`).

## 9. Data and model findings worth knowing

* NRES > total occurs in **zero** cells of GHS-BUILT-S R2023A over India (counted in every run report; the clipping logic is tested with a synthetic inconsistency).
* 29.5–30.2 % of parents (8.2–9.7 % of the population) contain no GHSL residential surface and keep WorldPop's own distribution (fallback). Table above.
* **WorldPop 2010 differs from the other 10 years:** the file is one column wider (35076 vs 35075) on the same lattice, and marks 1,114,365 additional cells as valid, holding 4,311 people in total (99.98 % zeros). It was registered onto the common grid by a verified whole-pixel offset; 31 valid cells with 0 people fall outside the reference grid and are reported (`outside_reference_grid`); the QA gate `no_population_outside_reference_grid` would fail the year if they held population. The extra footprint explains why 2010 has 35,280 more populated parents.
* **State and district totals are not exactly conserved** (parents cross admin borders): state max 2.7 %, district median 0.11 %, 99th percentile 2.0 %, max 18 % (a tiny unit). National and per-parent conservation is exact. `eval/README.md`.
* WorldPop-India's footprint and GHS-POP's differ by about 1.1 % of the population (12–14 million people), plus Level 1 holds about 1.1–1.2 million people outside any geoBoundaries ADM1 polygon. Both are reported per year; the locations were not attributed cell by cell.
* Level 1 totals are 3–4 % below GHS-POP's in the same cells because the two sources have different national totals (WorldPop is not UN-adjusted).

## 10. API/schema/contract deviations and cross-agent change requests

* **No deviation** from the v1 manifest contract or the frozen interfaces. Manifest entries use only supported fields, and the repository's unmodified validator and importer accept them.
* Request to **Agent 1** (`docs/change-requests/agent2-to-agent1-inputs.md`): adopt or replace the fallback inputs; keep the WorldPop native grid and GHSL `4326_3ss` product; supply the 2015 epoch and GHS-POP; authoritative Census-2011 boundaries (needed only for exact district conservation or the optional zone-parent mode).
* Request to **Agent 4** (`docs/change-requests/agent2-to-agent4-platform.md`): optional `science` Compose profile; capacity for 11 national float32 COGs (13.2 GB; the importer writes its own COG copy per dataset into the assets volume, see the measured import size above, so allow about that much again); shared legend scale across years.
* Windows note for integrators: a PostgreSQL install that sets `PROJ_LIB` can shadow rasterio's `proj.db`, which makes the **unmodified importer** report `unsupported_crs` for any raster. `cgis_level1` and the import launcher used here point PROJ at rasterio's bundled data. This is an environment issue, not a data defect, and it does not arise in the Linux containers.
* **Importer defect on Windows hosts (Agent 4's code, not changed by Agent 2):** `ingestion/importer.py::_fsync_file` calls `os.fsync` on a read-only handle, which raises `OSError: [Errno 9] Bad file descriptor` on Windows after the COG is written (observed natively for 2001 and 2002). It works in the Linux container. Proposed fix and tests in `docs/change-requests/agent2-to-agent4-platform.md` section 4.

## 11. Exact commands and results

```sh
cd science/level1 && python -m pytest -q                       # 47 passed (local); 47 passed (container, --memory=16g)
science/level1/scripts/fetch_inputs.sh data/incoming           # open inputs (resumable)
export PYTHONPATH=science/level1
python -m cgis_level1 inventory --config science/level1/config/india_full.toml
python -m cgis_level1 run       --config science/level1/config/india_full.toml     # 11 years, each status ok
python -m cgis_level1 evaluate  --config science/level1/config/india_full.toml
python science/level1/scripts/spot_check.py --year 2003 --n 40 --seed 2003
python -m cgis_level1 evidence  --config science/level1/config/india_full.toml --label india-full
# Delhi sample: --config science/level1/config/india_sample_delhi.toml   (run + evaluate + evidence --label sample-delhi)
```
`science/level1/scripts/run_all.sh` chains tests, inventory, run, evaluate, validate, summary and evidence.

## 12. Blockers, unresolved risks and limitations

* **Blockers: none for Level 1.** All inputs were open, and the one human decision was approved.
* **Not done by Agent 2 (by design):** import into the *project's* Compose stack (that is Agent 7's integration run; here the same importer and migrations were run against a disposable PostGIS container built from the repo's pinned image); admin-zone parent mode with authoritative boundaries (needs Agent 1's boundaries); Level 2 (Agent 3).
* **Limitations:** GHS-POP is a model, not truth, interpolated for 9 of 11 years; state/district conservation is approximate (above); 2011 depends on the approved GHSL-2015 epoch; WorldPop 2010 has a different valid footprint; the national run was measured on one Windows host with 31 GB RAM, so "fits in 16 GB" rests on the measured 4.3 GiB production and 6.6 GiB evaluation peaks and the enforced 12 GiB budget, not on a run inside a 16 GB-limited machine; sampling interval 0.25 s can miss very short spikes.
* **Hygiene:** `data/` (inputs, work, outputs) is git-ignored; no restricted data, secrets or COGs are committed.

## 13. Integration instructions for Agent 7

1. Check `git merge-base --is-ancestor c4dac59 <tip>`; the branch touches only `science/level1/`, so merge conflicts with other agents are not expected.
2. Real products must be regenerated or copied from the operator's `data/outputs/level1/` (13.2 GB, SHA-256 in the table above) and imported with the unchanged importer:
   `docker compose run --rm -v "$PWD/data/outputs/level1:/data/incoming/level1:ro" ingestion import --manifest /data/incoming/level1/manifest.json`.
3. For a stack without the real COGs, `examples/manifest.synthetic-level1.json` (+ `make-synthetic` and `run` with `synthetic.toml`) gives 11 SYNTHETIC `synthetic-india-l1-pop-YYYY` datasets.
4. Re-run `python -m pytest -q` in `science/level1` and `python -m cgis_level1 summary --config science/level1/config/india_full.toml`.

## 14. Acceptance rows (labels: PASS / FAIL / BLOCKED / NOT_RUN)

| ID | Label | Evidence |
|---|---|---|
| S1: 11 real India-wide Level 1 COGs, georeferencing, NoData, checksums, provenance, catalog registration | **PASS** | Section 4 table; unmodified validator 11/11; catalog import 11/11 + idempotent re-import 11/11 `unchanged` + catalog rows verified (section 5); against a disposable catalog, project-stack import is Agent 7's |
| S2: residential subtraction, 2001–2011 weighting, 2011 decision logged, mass-balance and seam tests | **PASS** | Methods and tests (47), run reports (`checks` all true for 11 years), spot-checks 11/11, seam test, D-L1-01 approved and logged |
| S3: evaluation vs GHS-POP on matched support with maps, metrics, limitations; no fabricated score | **PASS** | `eval/` bundles for 11 years + figures + `eval/README.md`; honest reading includes the Pearson/RMSE weakness |

The A7 row I4 (independent scientific spot-checks) is Agent 7's to mark; the inputs for it (spot-check script, bundles, SHAs) are provided here.
