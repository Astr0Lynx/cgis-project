# Data readiness report (Agent 01) — overall: **BLOCKED on D3; D1, D2, D4 evidenced**

Measured 2026-11-09 on one Linux host (14 GB RAM, Docker 15 GB, 734 GB free disk). Real source bytes only; synthetic fixtures appear
solely in unit tests. Machine-readable detail: `science/inputs/national_validation.json`, `science/docs/source_inventory.json`.

| Gate | Status | Evidence |
|---|---|---|
| **D1** WorldPop 2001–2011, checksums, provenance, gaps | **PASS** | All 11 `ind_ppp_YYYY.tif` downloaded, size = server Content-Length, sha256 recorded (`source_state.json`), **all 11 identical to Agent 2's independently downloaded files**; no duplicate year files. Grid: 10 years 35075×34497, **2010 is 35076 wide** on the same lattice (offset −1 col, registered whole-pixel, not resampled). Gaps: on a 1/16 decimated sample, 4.1 % of cells inside the (claimed-extent, diagnostic) geoBoundaries ADM1 footprint are NoData, ~2.6 k sampled cells hold population outside it; Andaman & Nicobar and Lakshadweep contain data in every year. Gap attribution cell-by-cell was **not** done, and the footprint is not authoritative. |
| **D2** GHSL total/NRES (2000/05/10) + GHS-POP, units, CRS | **PASS** | GHS-BUILT-S, NRES and GHS-POP R2023A `4326_3ss` for 2000/2005/2010 **and the approved extra epoch 2015** (12 products): India tiles R6–R9 × C25–C28 → 14 present + 2 open-sea absent (R8_C25, R9_C25, HTTP 404) per product, 0 problems. Every tile: EPSG:4326, on WorldPop's lattice, units bounds checked. **168 downloaded tiles are byte-identical (tif sha256) to Agent 2's**, 0 differ, 9 have no Agent 2 entry. CRS reconciliation tests: lattice offset, Mollweide→4326 area-conserving fallback, categorical nearest. |
| **D3** Census 2011, Census-coded polygons, Bhuvan 2011–12 LULC | **BLOCKED** | None obtained. Human actions B1–B3 and decisions B5 in `BLOCKER_REQUEST.md`; Agent 3 request in `science/inputs/CANDIDATE_TOWNS_REQUEST.md`. geoBoundaries ADM1 (36) / ADM2 (735) fetched and integrity-checked but carry **no Census codes**. Join/overlap/hole checks are implemented and tested only on synthetic geometry. |
| **D4** disk/time/RSS budget, licences, nothing restricted in Git | **PASS (data-preparation scope)** | See budget below. Licence register in `source_inventory.json`; `/data/` git-ignored; commit contains no rasters, no Bhuvan links/credentials. |

## Real regional sample (Hyderabad region, bbox 77.9–79.1°E, 16.9–17.9°N; tile R8_C26)
- 7 rasters on one 1441×1201 EPSG:4326 grid: WorldPop 2011 (Σ = 11,168,120 people; 1,699,796 valid, 30,845 NoData; counts unchanged) and GHSL total/NRES 2000/05/10 (uint16, NoData 65535 declared because the source declares none; values ≤ 8,188 m² = cell area; aligned window copy, **no resampling**, offsets verified). Σ built-up m²: total 233.5 M / 264.1 M / 303.9 M, NRES 9.2 M / 10.7 M / 12.6 M (2000/05/10).
- Vector sample: 11 geoBoundaries ADM2 district polygons (diagnostic, not Census) with manifest.
- **Original ingestion `validate`: 7/7 raster + 1/1 vector `validated`. Original `import` (disposable repo-pinned PostGIS 17-3.5 container, migrations applied): 7/7 rasters `imported`, status `ready`; vector imported (11 admin units); re-running import reports `unchanged` for all (idempotent).** Logs: `science/inputs/examples/*.import.jsonl`.
- Resources: sample build 7.5 s / 274 MB RSS; import 12.5 s / 167 MB RSS.

## National budget (measured, not extrapolated, unless stated)
| Item | Value |
|---|---|
| WorldPop 11 years | 20.2 GB. HTTPS ≈ 1.3 MB/s (≈25 min/file, **no resume** → a dropped connection restarts); official FTP mirror ≈ 3–5 MB/s per stream (≈6–8 min/file, resumable) |
| GHSL India tiles (12 products × 14) | total 0.98 GB, NRES 0.10 GB, POP 3.5 GB (zips) |
| `data/incoming` total | 24 GB |
| National integrity run (11 years decimated coverage + 168 tile opens) | 650 s, **950 MB peak RSS** (decimated reads, no full-array loads) |
| Harmonisation (window copy / crop) | block-wise 1024²; sample peaks 274 MB. Full-India copy is the same code path; time not measured at national scale (only the integrity pass was). |
| 16 GB target | The measured peaks (≤1 GB for Agent 1 tooling) leave the 16 GB budget to Agent 2's pipeline (their measured 4.3–6.6 GiB, enforced 12 GiB cap; their host had 31 GB). |

## Known limitations / risks
- WorldPop 2010 covers an extra west column and extra valid cells (reported by Agent 2, confirmed here).
- The 4.1 % NoData-in-footprint figure uses a claimed-extent diagnostic footprint and a decimated sample.
- GHSL `4326_3ss` is JRC-resampled from the native Mollweide product; its resampling was not audited here.
- Absolute local paths appear in `source_state.json` (path strings only).
- The 2011 weights depend on the 2015 epoch (D-L1-01 option C, approval recorded by Agent 2 as the project owner's chat reply; Agent 7 may want to confirm).
