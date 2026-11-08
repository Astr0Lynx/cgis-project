# Level 1 inputs: sources, licences, access and checksums

Machine-readable inventory (every file with size, SHA-256, CRS, transform, NoData):
`inputs_inventory.india-full.json` (real) and `inputs_inventory.synthetic.json` (SYNTHETIC fixtures).
Regenerate it with `python -m cgis_level1 inventory --config science/level1/config/india_full.toml`.

**Access state:** all Level 1 inputs are open data with no access controls. They were downloaded as a
documented lawful fallback (`scripts/fetch_inputs.sh`) because Agent 1 deliveries were not yet available.
See `docs/change-requests/agent2-to-agent1-inputs.md`. No restricted data is used. No input bytes are
committed.

| Input | Product / version | Units, grid | Licence and citation | Source |
|---|---|---|---|---|
| WorldPop counts 2001–2011 | WorldPop Global1, *unconstrained*, individual countries 2000–2020, `ind_ppp_YYYY.tif` (people per pixel, not UN-adjusted) | people per cell; EPSG:4326, 3″, 35075 × 34497, float32, NoData −99999 | CC BY 4.0. WorldPop (www.worldpop.org, School of Geography and Environmental Science, University of Southampton; Department of Geography and Geosciences, University of Louisville; Departement de Geographie, Universite de Namur) and Center for International Earth Science Information Network (CIESIN), Columbia University (2018). Global High Resolution Population Denominators Project. doi:10.5258/SOTON/WP00645 | `https://data.worldpop.org/GIS/Population/Global_2000_2020/YYYY/IND/ind_ppp_YYYY.tif`, identical bytes via `ftp://ftp.worldpop.org/…` |
| GHSL built-up surface, total | GHS-BUILT-S R2023A, `GHS_BUILT_S_E{2000,2005,2010,2015}_GLOBE_R2023A_4326_3ss_V1_0`, tiles R6–R9 × C25–C28 (R8_C25 and R9_C25 do not exist: sea) | m² of built-up surface per cell; EPSG:4326, 3″, uint16, 0 = none | CC BY 4.0. Pesaresi M., Politis P. (2023): GHS-BUILT-S R2023A — GHS built-up surface grid, derived from Sentinel2 composite and Landsat, multitemporal (1975–2030). European Commission, JRC. doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA | `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/` |
| GHSL built-up surface, non-residential | GHS-BUILT-S **NRES** R2023A, same epochs, tiles and grid | m² of non-residential surface per cell | CC BY 4.0, same citation and DOI | same directory, `GHS_BUILT_S_NRES_E…` |
| GHS-POP (evaluation reference only) | GHS-POP R2023A, `GHS_POP_E{2000,2005,2010,2015}_GLOBE_R2023A_4326_3ss_V1_0` | people per cell; EPSG:4326, 3″, float64 | CC BY 4.0. Schiavina M., Freire S., Carioli A., MacManus K. (2023): GHS-POP R2023A — GHS population grid multitemporal (1975–2030). European Commission, JRC. doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE | `https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/` |
| State zones (diagnostic) | geoBoundaries gbOpen IND ADM1, commit 9469f09 (DataMeet / Election Commission of India, boundary year 2011), 36 units | polygons, CRS84 | CC BY 2.5 IN | `https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM1/geoBoundaries-IND-ADM1.geojson` |
| District zones (diagnostic) | geoBoundaries gbOpen IND ADM2, commit 9469f09 (Pathways Data / LGD, boundary year 2021), 735 units | polygons, CRS84 | ODbL 1.0 | `…/gbOpen/IND/ADM2/geoBoundaries-IND-ADM2.geojson` |

## Notes on the sources

* **Lattice.** WorldPop-IND and GHSL 4326_3ss share one 3″ lattice (offset 0.000 px, verified in code),
  so no weight or count regridding is needed.
* **Boundary semantics.** The geoBoundaries ADM1/ADM2 layers depict India's claimed boundary, including
  areas where WorldPop-IND is probably NoData (e.g. parts of Jammu & Kashmir/Ladakh; not verified cell by cell).
  GHS-POP population there is reported as *outside the Level 1 mask*. It is a coverage-definition difference, not a model loss.
  These zones are diagnostic only and are not authoritative Census 2011 boundaries (requested from Agent 1).
* **Year/epoch matching.** Level 1 year YYYY uses WorldPop YYYY. GHSL weights are interpolated from 5-year
  epochs (see METHOD.md). The reference is GHS-POP at the same year (exact for 2005/2010, otherwise
  linearly interpolated between epochs).
