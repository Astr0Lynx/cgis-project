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

## B2 — Census of India 2011 PCA (D3) — **PARTLY RESOLVED by Agent 1 on 2026-11-10; village/town level still open**

- **Finding (tested, not assumed):** the Census NADA catalog (`https://censusindia.gov.in/nada/`) is browsable and its table downloads are served
  **without login** (HTTP 200 on `.../catalog/6191/download/9268/DDW_PCA0000_2011_Indiastatedist.xlsx`). Its login pop-ups concern staff systems.
- **Done:** India/State/District PCA (640 districts, Σ TOT_P = 1,210,854,977 = official India total) downloaded, checksummed, registered as
  `india-census-2011-pca-state-district`.
- **Still needed:** **village/town-level PCA for the selected towns' districts.** These are per-district downloads in the NADA catalog
  (District Census Handbook Part B / PCA workbooks). I did not fetch any because the districts depend on Agent 3's town list (B4).
  Once the list exists this is a scripted, login-free fetch: no human step expected. (If a particular file turns out to require login, I stop and report.)
- **Terms:** no licence text was found programmatically. Verify the portal's terms before redistributing; Git carries checksums only.

## B3 — Boundaries aligned to Census 2011 codes (D3) — **PARTLY RESOLVED; town/village polygons still open**

- **Done:** DataMeet `Districts/Census_2011` (CC BY 4.0, community-digitised, **not** an official SoI/Census product): 641 polygons carrying
  `ST_CEN_CD`, `DT_CEN_CD` and `censuscode` (the national sequential district code = the PCA "District" column). Real join
  (`python -m cgis_data census-join` -> `science/inputs/census_district_join.json`): **640/640 Census districts matched 1:1**, no state-code
  mismatches, no duplicate codes; 1 extra polygon ("Data Not Available", code 0); **1 invalid geometry (Cuddalore: ring self-intersection)**
  that the importer would reject, so it must be repaired (and documented) before ingest. District level only.
- **Still needed:** **town and village polygons** with Census codes for the selected towns. DataMeet does not provide them. Candidates remain unverified;
  Survey of India / Bhuvan village boundaries are likely restricted. **Decision for you:** which source is acceptable, or supply licensed files.
- geoBoundaries ADM1/ADM2 stay diagnostic only (no Census codes).

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
