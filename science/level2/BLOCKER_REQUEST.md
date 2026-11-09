# Inputs and decisions needed for real Level 2

## Bhuvan analytical data and permissions

The official announcement confirms that the **2011–12 1:50K shapefiles** are provided after the AOI request is approved. The download link goes to the requester’s registered address. Agent 3 verified the public instructions and prepared the candidate IDs, five request AOIs and purpose text. An authorized operator can submit that request and place the approved files in local ignored storage for Agent 1 to prepare. Include the delivered legend, mapping scale and documented positional/thematic accuracy if available, approved use, and permission for derived product distribution. Ask NRSC to confirm that the proposed 23.5 m modeled grid and settlement-scale visualizations fall within the authorized use. The published web-product technical report specifies a scale-of-use restriction. [NRSC technical report](https://bhuvan-app1.nrsc.gov.in/2dresources/thematic/2LULC/lulc1112.pdf) Keep credentials and download links private. [Candidate AOIs and purpose](SELECTION.md) · [Bhuvan request instructions](https://bhuvan.nrsc.gov.in/updates/bhuvan_apr2026.html) · [Request portal](https://bhuvan-app1.nrsc.gov.in/thematic/thematic/index.php)

If approval is denied for a candidate, use the same predeclared ranking and request an eligible alternate AOI. A different analytical LULC product would require an explicit scientific decision and traceability update.

## Census polygons and Level 1

- **Agent 1:** provide legally usable 2011 settlement polygons keyed to the exact PCA units in the selection, with evidence of matching vintage and municipal or village extent. Official tabular counts are already available for the candidate list. See the [Census Population Finder](https://censusindia.gov.in/census.website/en/data/population-finder).
- **Agent 2:** provide a local copy of `india-l1-pop-2011` and the matching v1 manifest, with the manifest-relative `cog/` path intact. Agent 2's handoff at `996d71a1cfe6bb4f3cca4129f88042e4494ccd8d` reports a national SHA-256 of `8284c2877c49d7329ea6b6775448a265c2403722f24466bb1ae218bdd34d3488`. Git carries metadata and evidence. The raster is stored separately. The national run verifies the raster bytes against the accepted manifest.

## Scientific decisions

**D-L2-01** records the user's 9 November 2026 approval of the hard residential/settlement mask. Verify and record the exact code mapping when the authorized analytical legend arrives. Refer ambiguous mixed-use categories for a decision if they would affect the primary allocation. Soft class weights keep some population on potentially habitable non-settlement classes for the sensitivity analysis. A hard mask concentrates population on designated settlement classes and may be infeasible for small rural sites where LULC misses their built-up area. Synthetic tests exercise both with a toy legend.

**D-L2-02** records how the flowchart's delta threshold is used. Report observed residuals and coverage by default. Any threshold used to label cases for the grouped product needs a predefined metric, denominator, coverage requirement and signed cutoff. A Level 1 comparison measures model agreement with a related prior. Use it for diagnostics and keep inclusion tied to the predeclared sampling rubric.
