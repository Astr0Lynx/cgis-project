# Request to Agent 3: candidate towns/villages (C1 checkpoint) — from Agent 1

Agent 1 cannot scope the Bhuvan LULC request, the Census join test or the polygon request without this list.
**No towns have been chosen here; Agent 3 owns the selection and its scientific rationale.**

Please supply `science/inputs/candidate_towns.csv` (template: `candidate_towns.template.csv`) with **≥ 6** settlements:

- spread across different parts of India (north/south/east/west/central/north-east) and settlement types
  (large city, small/medium town, peri-urban, rural village), chosen by a stated, non-outcome-based rule;
- each with its **Census 2011 identifiers** (state, district, sub-district, town or village code), name, a representative lon/lat
  and the Census 2011 population if already known;
- **at most 5 districts per Bhuvan request** (portal limit): group towns accordingly in `bhuvan_request_group`;
- a spare (backup) town per class, because LULC approval may not be granted for every district.

Agent 1 will then (a) fill the Bhuvan request (BLOCKER_REQUEST.md B1) for the listed districts, (b) run the Census join and
polygon-integrity tests (`check_census_join`, `check_polygons`) once Census/boundary files exist, and (c) report any town
for which no lawful LULC can be obtained so Agent 3 can replace it **before** the set is finalised.
