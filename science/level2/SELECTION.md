# Settlement selection and Agent 1 request

The sampling frame is a purposive, stratified set of 2011 settlement units. The six priority-1 candidates in [selection.csv](selection.csv) cover five regions and five districts. The four priority-2 candidates are reserves within those districts. The sample is intended to test contrasting settings. It cannot estimate national accuracy.

Freeze priority before computing any comparison metrics. Aim for two urban cores, two urban-fringe cases and two rural villages across at least four regions. Use the working size strata <5,000, 5,000–99,999 and ≥100,000 people, and preserve all three when feasible. A Census Town can represent a peri-urban morphology in this design. The Census residence column remains separate from the proposed morphology. Agent 1 should confirm fringe status and record the 2011 built-up context before selection. The rationales describe expected land-cover contrasts. Actual LULC categories are unknown until authorized data arrive.

1. Verify a candidate has a unique 2011 PCA record with a polygon for the same census support. Keep the 2-digit state, 3-digit district, 5-digit subdistrict and 6-digit settlement components as strings. Use the non-ward settlement row, with ward and enumeration-block fields zero. For Chandigarh and Panaji, the municipal corporation and the municipal corporation plus outgrowths have the same code. Use the explicit municipal-only row and require matching support. Reject a PCA extract that retains both rows under one join key.
2. Verify the geographic context and that approved Bhuvan 1:50K LULC 2011–12 polygons plus the delivered legend cover the site. Check invalid polygons, overlaps, NoData, mixed classes and source scale. Record coverage and broad LULC classes before running the model.
3. Take all eligible priority-1 entries. For a failed slot use an eligible reserve in the same morphology and size stratum first, then in the same region where possible. If more than one qualifies, sort by priority and Census ID. Verify at least six sites, three morphology strata and four regions after replacement. Add and rank new candidates before modeling if the reserve set cannot satisfy these conditions. Record all exclusions and exceptions with the source-quality reason. Model residuals do not affect candidate inclusion.
4. Save the frozen decision table locally as `data/incoming/level2/selected.csv` with status `selected` and save the matching RFC 7946 `study_areas.geojson` there. Record source access and reproduction instructions with both. Only share geometries if the source terms allow it. The checked-in candidate list is for request preparation.

## Request-ready district AOIs

The Bhuvan portal accepts up to five districts. Select **Chandigarh (04/055), Varanasi (09/197), East Khasi Hills (17/298), Mysore/Mysuru (29/577), North Goa (30/585)** using their corresponding portal names. Codes here are Census 2011 codes and may differ from current portal identifiers. District selection is an access AOI. It does not define the settlement polygon used for allocation. If requesting smaller AOIs, derive them from the verified 2011 settlement geometries with a one-native-Level-1-cell margin. [Bhuvan 1:50K request portal](https://bhuvan-app1.nrsc.gov.in/thematic/thematic/index.php) · [NRSC request instructions](https://bhuvan.nrsc.gov.in/updates/bhuvan_apr2026.html)

### Purpose text for the authorized requester

Academic CyberGIS coursework. We request 2011–12 LULC 1:50K analytical polygons and the matching class legend for selected 2011 Census settlements in the five districts listed above. We will produce per-settlement population estimates on a 23.5 m target grid and compare with Census and Level 1 on matched spatial support. Please indicate permitted uses for the analytical source, derived rasters, evaluation plots and local teaching deployment.

## PCA audit

Counts were checked directly in the ORGI workbooks, sheet `Data` or the first data sheet, by the location-code components, `Level`, `Name`, `TRU`, ward `0000`, enumeration block `000000` and `TOT_P`. Trailing name whitespace was stripped. See the [Census Location Code Directory](https://censusindia.gov.in/nada/index.php/catalog/42648) for code semantics. The official [PCA state/district table](https://censusindia.gov.in/nada/index.php/catalog/6191) corroborates district IDs. No workbook is tracked.

| Census workbook | Download SHA-256 |
| --- | --- |
| [ORGI Basic Population Figures, 42559](https://censusindia.gov.in/nada/index.php/catalog/42559) | `da487f7a1181fd9f43ccf354a8b5864ffca5920fc82b5183d644d483445cb380` |
| [Varanasi PCA TV, 6388](https://censusindia.gov.in/nada/index.php/catalog/6388) | `199155439dcc8744ec7deed0e0992e6423b8e510b5448e8847129d5bbd75608e` |
| [East Khasi Hills PCA TV, 6489](https://censusindia.gov.in/nada/index.php/catalog/6489) | `fcbdbaea1ffcacb973f3ca9802bda3ce7521b2e7dfe810ba4abb6168458dc73b` |
| [Mysore PCA TV, 6768](https://censusindia.gov.in/nada/index.php/catalog/6768) | `922cb937a5292e5d627b3ac8b8b18501cc93eee957515ce904cf04a73b8bbaea` |
| [North Goa PCA TV, 6776](https://censusindia.gov.in/nada/index.php/catalog/6776) | `2983122e73fca540e9c65057a9f7fbc9fdaa8848761315818a37f5f0fea45763` |

## Public settlement-boundary lead

The SHRUG 2.2 PC11 village/town shapefile attribute table has one exact composite Census-code match for each of the six primary candidates and four reserves in [selection.csv](selection.csv). Chandigarh and Panaji both have `mdds_og=1` in that table. Check actual municipal-only extent against the selected PCA records and verify the geometry and source limitations for each site before adopting the polygons. The publisher lists the dataset under CC BY-NC-SA 4.0. [SHRUG downloads and terms](https://www.devdatalab.org/shrug_download/) · [Boundary source and limitations](https://docs.devdatalab.org/SHRUG-Construction-Details/shrug-open-source-polygons/)
