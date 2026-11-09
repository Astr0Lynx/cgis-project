# Human-action requests (Agent 01) — nothing below is bypassed or scripted

Status legend: every item is **BLOCKED** until a human completes the stated action. Unaffected work continues.

## B1 — Bhuvan LULC 1:50,000, 2011–12 (blocks D3, S5)

- **Required item:** NRSC/Bhuvan LULC 50K shapefiles, agricultural year **2011–12**, for the Level 2 towns' districts.
- **Official source:** <https://bhuvan.nrsc.gov.in/updates/bhuvan_apr2026.html> (announcement dated 2026-04-16) →
  LULC-50K download application at <https://bhuvan-app1.nrsc.gov.in/thematic/thematic/index.php>.
  The announcement states (under National Geospatial Policy-2022) that 2015–16, **2011–12** and 2005–06 shapefiles are
  requested by selecting **up to five districts**, drawing an AOI/bounding box, or uploading an AOI (shapefile/KML),
  must state the **purpose of utilisation**, and that a download link is emailed to the registered address after approval.
- **Why necessary:** Level 2 weighting needs a categorical LULC for 2011–12 (flowchart); no other source is equivalent.
- **Lawful steps already taken:** read the public announcement only; no login attempted, no bulk scraping.
- **Exact operator action:** (1) Register/log in to Bhuvan with your own account. (2) Open the LULC-50K download page.
  (3) For each batch of ≤5 districts (list from Agent 3's town selection: `science/inputs/candidate_towns.csv`,
  template and request in `science/inputs/CANDIDATE_TOWNS_REQUEST.md`; **not yet supplied**) choose year **2011–12**, type of selection = "District". (4) Paste the purpose text below. (5) After
  the approval email arrives, download the zips into `data/incoming/india-bhuvan-lulc-50k-2011-12/` (git-ignored) and tell
  Agent 1; do **not** share the emailed link or credentials in chat or Git.
- **Suggested purpose text:** "Academic research (CyberGIS course project; [INSTITUTION — operator to fill in]). 2011–12 LULC
  will be used only as a categorical weighting layer to spatially disaggregate Census 2011 town/village population to a
  ~23.5 m grid for a small set of research towns. Outputs are research products; the source shapefiles will not be
  redistributed; Bhuvan/NRSC will be cited."
- **Realistic impact:** without it Level 2 (S4–S6) is BLOCKED; Level 1 is unaffected. Approval time is unknown.
- **Safe alternatives (need explicit scientific approval, not substituted silently):** (a) Bhuvan 2015–16 LULC (different
  period from Census 2011; documents a temporal mismatch); (b) ESA WorldCover / ISRO-NRSC open global land cover
  (different class scheme and date; not the flowchart's product); (c) choose towns whose districts are obtainable.
  Each changes class legend, date and scientific validity; none is "the Bhuvan 2011–12 product".

## B2 — Census of India 2011 Primary Census Abstract (blocks D3)

- **Required item:** PCA 2011 town/village totals with state/district/sub-district/town-village codes (all India, or at
  least the states of the selected towns).
- **Official source:** <https://www.censusindia.gov.in/census.website/en/data/population-finder> and
  <https://censusindia.gov.in/census.website/data/census-tables> (HTTP 200 verified; table downloads are portal driven).
- **Operator action:** download the state-wise PCA (village directory and town) tables from the portal terms-compliantly into
  `data/incoming/india-census-2011-pca/` and tell Agent 1. Check the portal terms for redistribution; Git receives only
  checksums and acquisition steps.
- **Impact:** blocks Level 2 census constraint and Level 1/2 census comparisons. Safe alternative: none equivalent.

## B3 — Boundaries aligned to Census 2011 codes (blocks D3 join test)

- **Required item:** national state/district/sub-district polygons plus town/village polygons for the selected towns, with
  Census 2011 geocodes, valid geometry, known CRS and a licence that permits research use.
- **Status update:** geoBoundaries ADM1 (36) / ADM2 (735) were downloaded and pass geometry/ID checks, but have **no
  Census 2011 codes**: they are diagnostic zones only (Agent 2 uses them the same way). The real requirement remains open.
- **Candidates (not yet verified as Census-coded; do not treat as chosen):**
  - DataMeet community maps, CC BY 4.0 — <https://github.com/datameet/maps> (HTTP 200 verified). District/sub-district
    layers exist; **town/village polygons and Census-code coverage must be verified**.
  - geoBoundaries (CC BY 2.5 IN / ODbL) — fetched, diagnostic only; ADM boundaries are *not* Census codes.
  - Survey of India / Bhuvan village boundaries — likely restricted; requires a human application.
- **Operator action:** decide which boundary source is acceptable for the project (this is a scientific/licensing
  decision), or supply the licensed files. Agent 1 will then run the 1:1 join/overlap/hole tests already implemented
  (`cgis_data.validate.check_census_join`, `check_polygons`, `coverage_holes`).
- **Impact:** without Census-code-aligned polygons no unambiguous town joins (D3) can be claimed.

## B4 — Candidate towns from Agent 3 (coordination, not a blocker on data fetching)

Agent 3 must supply **≥6** diverse towns with Census codes **early** so B1 requests can be scoped to ≤5 districts per
request. Until that list exists no Bhuvan request can be targeted. (Agent 3 is a separate agent; this branch could not
contact it, and no `completion/agent3-*` branch existed on origin when last checked — Agent 7 should relay.)

## B5 — Two mismatches between the original flowchart and the 2026 sources (decision needed; nothing substituted)

1. **Level 1 raster count.** The handwritten page 2 says "10 Level1 rasters (1 for each year 2001–2011)"; 2001–2011 inclusive is **11**
   years. The coordinator's frozen decision (PROJECT_CONTEXT) is 11, which this branch follows. Please confirm that 11 is intended.
2. **Level 2 LULC product.** Page 1 lists "Bhuvan LULC [23.5 m, 2011]", i.e. a ~23.5 m **raster**. The 2026 Bhuvan announcement offers LULC **1:50,000
   shapefiles** for 2011–12 (vector). Whether Bhuvan offers a native ~23.5 m LISS-III-based raster for 2011–12 has **not been verified by
   Agent 1** (portal access needed). Options for the human: (a) accept the 1:50K vector (rasterised to a 23.5 m target grid, no extra
   detail), (b) obtain a genuine 23.5 m raster product if Bhuvan provides one under permission. Consequence: determines the Level 2 method's source resolution claim.
