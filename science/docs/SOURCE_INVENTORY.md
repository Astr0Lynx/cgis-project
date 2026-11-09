# Source inventory (generated – do not edit by hand)

Regenerate: `cd science/data && PYTHONPATH=. python -m cgis_data inventory`. Sizes for GHSL rows are the GLOBAL zip; India needs only the tiles listed in `tiles` (see HARMONIZATION.md). `sha256` is blank until fetched.

| source_id | year | access | units | CRS | size (bytes) | sha256 | license |
|---|---|---|---|---|---|---|---|
| india-worldpop-unconstrained-2001 | 2001 | **available** | people_per_cell | EPSG:4326 | 1838961247 | 9df5687737cc | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2002 | 2002 | **available** | people_per_cell | EPSG:4326 | 1839076965 | 7e74d502fe19 | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2003 | 2003 | **available** | people_per_cell | EPSG:4326 | 1839301688 | 9a6258db4716 | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2004 | 2004 | **available** | people_per_cell | EPSG:4326 | 1831889526 | 4e78bb14a84b | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2005 | 2005 | **available** | people_per_cell | EPSG:4326 | 1840223955 | adb011731cfa | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2006 | 2006 | **available** | people_per_cell | EPSG:4326 | 1838792857 | 88ea6ef18ea8 | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2007 | 2007 | **available** | people_per_cell | EPSG:4326 | 1835812597 | 3d43343eff48 | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2008 | 2008 | **available** | people_per_cell | EPSG:4326 | 1842319542 | 7b8ae695dfb2 | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2009 | 2009 | **available** | people_per_cell | EPSG:4326 | 1844404374 | f52e260f049e | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2010 | 2010 | **available** | people_per_cell | EPSG:4326 | 1847017335 | ca8d2677fc78 | CC BY 4.0 (WorldPop |
| india-worldpop-unconstrained-2011 | 2011 | **available** | people_per_cell | EPSG:4326 | 1849514894 | ff6a2b3e57af | CC BY 4.0 (WorldPop |
| india-ghsl-built-total-2000 | 2000 | **available** | m2_per_cell | EPSG:4326 | 1631851494 |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-total-2005 | 2005 | **available** | m2_per_cell | EPSG:4326 | 1723954874 |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-total-2010 | 2010 | **available** | m2_per_cell | EPSG:4326 | 1812507992 |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-total-2015 | 2015 | **available** | m2_per_cell | EPSG:4326 |  |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-nres-2000 | 2000 | **available** | m2_per_cell | EPSG:4326 | 164742747 |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-nres-2005 | 2005 | **available** | m2_per_cell | EPSG:4326 | 169052032 |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-nres-2010 | 2010 | **available** | m2_per_cell | EPSG:4326 | 172625104 |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-built-nres-2015 | 2015 | **available** | m2_per_cell | EPSG:4326 |  |  | CC BY 4.0 (European Union, JRC |
| india-ghsl-pop-2000 | 2000 | **available** | people_per_cell | EPSG:4326 | 4148651870 |  | CC BY 4.0 (European Union, JRC) |
| india-ghsl-pop-2005 | 2005 | **available** | people_per_cell | EPSG:4326 | 4353050644 |  | CC BY 4.0 (European Union, JRC) |
| india-ghsl-pop-2010 | 2010 | **available** | people_per_cell | EPSG:4326 | 4555219066 |  | CC BY 4.0 (European Union, JRC) |
| india-ghsl-pop-2015 | 2015 | **available** | people_per_cell | EPSG:4326 |  |  | CC BY 4.0 (European Union, JRC) |
| india-geoboundaries-adm1 | 2011 | **available** | n/a (polygons) | EPSG:4326 (CRS84) | 46170854 | 47aa0acb6f69 | CC BY 2.5 IN (DataMeet / Election Commission of  |
| india-geoboundaries-adm2 | 2021 | **available** | n/a (polygons) | EPSG:4326 (CRS84) | 48317735 | 8bef6929fd65 | ODbL 1.0 (Pathways Data / LGD) |
| india-census-2011-pca | 2011 | **missing** | persons (census counts) | n/a (tabular) |  |  | Government of India open data |
| india-admin-boundaries-2011 | 2011 | **missing** | n/a (polygons) | EPSG:4326 expected |  |  | UNCONFIRMED – Survey of India / Census restricti |
| india-bhuvan-lulc-50k-2011-12 | 2011 | **restricted** | categorical LULC class codes | vector shapefile (CRS to be read from delivered .prj) |  |  | National Geospatial Policy-2022 access |

## Per-source details

### india-worldpop-unconstrained-2001
- Title: WorldPop India unconstrained individual-countries population count 2001 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2001/IND/ind_ppp_2001.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2002
- Title: WorldPop India unconstrained individual-countries population count 2002 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2002/IND/ind_ppp_2002.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2003
- Title: WorldPop India unconstrained individual-countries population count 2003 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2003/IND/ind_ppp_2003.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2004
- Title: WorldPop India unconstrained individual-countries population count 2004 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2004/IND/ind_ppp_2004.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2005
- Title: WorldPop India unconstrained individual-countries population count 2005 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2005/IND/ind_ppp_2005.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2006
- Title: WorldPop India unconstrained individual-countries population count 2006 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2006/IND/ind_ppp_2006.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2007
- Title: WorldPop India unconstrained individual-countries population count 2007 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2007/IND/ind_ppp_2007.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2008
- Title: WorldPop India unconstrained individual-countries population count 2008 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2008/IND/ind_ppp_2008.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2009
- Title: WorldPop India unconstrained individual-countries population count 2009 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2009/IND/ind_ppp_2009.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2010
- Title: WorldPop India unconstrained individual-countries population count 2010 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2010/IND/ind_ppp_2010.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-worldpop-unconstrained-2011
- Title: WorldPop India unconstrained individual-countries population count 2011 (100 m)
- URL: https://data.worldpop.org/GIS/Population/Global_2000_2020/2011/IND/ind_ppp_2011.tif
- Landing page: https://hub.worldpop.org/Global1_2000-2020
- Edition: WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)
- Licence: CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)
- Semantics: Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; aggregate by summation only.
- Mechanism: HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, so the whole file must be downloaded (resumable fetch uses Range only if honoured).

### india-ghsl-built-total-2000
- Title: GHSL GHS-BUILT-S R2023A total built-up SURFACE 2000 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2000_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_E2000_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. All built-up surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding.
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-total-2005
- Title: GHSL GHS-BUILT-S R2023A total built-up SURFACE 2005 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2005_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_E2005_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. All built-up surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding.
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-total-2010
- Title: GHSL GHS-BUILT-S R2023A total built-up SURFACE 2010 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2010_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_E2010_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. All built-up surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding.
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-total-2015
- Title: GHSL GHS-BUILT-S R2023A total built-up SURFACE 2015 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2015_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_E2015_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. All built-up surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding. Additional epoch authorised only for the 2011 Level 1 weights (D-L1-01 option C).
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-nres-2000
- Title: GHSL GHS-BUILT-S R2023A NRES built-up SURFACE 2000 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_NRES_E2000_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_NRES_E2000_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. Non-residential share of that surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding.
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-nres-2005
- Title: GHSL GHS-BUILT-S R2023A NRES built-up SURFACE 2005 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_NRES_E2005_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_NRES_E2005_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. Non-residential share of that surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding.
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-nres-2010
- Title: GHSL GHS-BUILT-S R2023A NRES built-up SURFACE 2010 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_NRES_E2010_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_NRES_E2010_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. Non-residential share of that surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding.
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-built-nres-2015
- Title: GHSL GHS-BUILT-S R2023A NRES built-up SURFACE 2015 (3 arc-second, EPSG:4326)
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_NRES_E2015_GLOBE_R2023A_4326_3ss/V1-0/GHS_BUILT_S_NRES_E2015_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php
- Edition: GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)
- Licence: CC BY 4.0 (European Union, JRC; attribution required)
- Semantics: Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. Non-residential share of that surface. Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding. Additional epoch authorised only for the 2011 Level 1 weights (D-L1-01 option C).
- Mechanism: HTTPS GET public JRC open-data FTP (global zip or per-tile zips)

### india-ghsl-pop-2000
- Title: GHSL GHS-POP R2023A population 2000 (3 arc-second, EPSG:4326) – evaluation reference only
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2000_GLOBE_R2023A_4326_3ss/V1-0/GHS_POP_E2000_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php
- Edition: GHS-POP R2023A, V1.0 (Schiavina et al. 2023, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE)
- Licence: CC BY 4.0 (European Union, JRC)
- Semantics: Modelled residential population COUNT per 3 arc-second cell (float), not density; a model product, not census ground truth.
- Mechanism: HTTPS GET public JRC open-data FTP

### india-ghsl-pop-2005
- Title: GHSL GHS-POP R2023A population 2005 (3 arc-second, EPSG:4326) – evaluation reference only
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2005_GLOBE_R2023A_4326_3ss/V1-0/GHS_POP_E2005_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php
- Edition: GHS-POP R2023A, V1.0 (Schiavina et al. 2023, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE)
- Licence: CC BY 4.0 (European Union, JRC)
- Semantics: Modelled residential population COUNT per 3 arc-second cell (float), not density; a model product, not census ground truth.
- Mechanism: HTTPS GET public JRC open-data FTP

### india-ghsl-pop-2010
- Title: GHSL GHS-POP R2023A population 2010 (3 arc-second, EPSG:4326) – evaluation reference only
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2010_GLOBE_R2023A_4326_3ss/V1-0/GHS_POP_E2010_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php
- Edition: GHS-POP R2023A, V1.0 (Schiavina et al. 2023, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE)
- Licence: CC BY 4.0 (European Union, JRC)
- Semantics: Modelled residential population COUNT per 3 arc-second cell (float), not density; a model product, not census ground truth.
- Mechanism: HTTPS GET public JRC open-data FTP

### india-ghsl-pop-2015
- Title: GHSL GHS-POP R2023A population 2015 (3 arc-second, EPSG:4326) – evaluation reference only
- URL: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2015_GLOBE_R2023A_4326_3ss/V1-0/GHS_POP_E2015_GLOBE_R2023A_4326_3ss_V1_0.zip
- Landing page: https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php
- Edition: GHS-POP R2023A, V1.0 (Schiavina et al. 2023, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE)
- Licence: CC BY 4.0 (European Union, JRC)
- Semantics: Modelled residential population COUNT per 3 arc-second cell (float), not density; a model product, not census ground truth. Reference for the 2015 epoch only if needed.
- Mechanism: HTTPS GET public JRC open-data FTP

### india-geoboundaries-adm1
- Title: geoBoundaries gbOpen IND ADM1 (DIAGNOSTIC ONLY – not Census 2011 codes)
- URL: https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM1/geoBoundaries-IND-ADM1.geojson
- Landing page: https://www.geoboundaries.org/
- Edition: geoBoundaries gbOpen, commit 9469f09 (same pin as Agent 2); 36 units expected
- Licence: CC BY 2.5 IN (DataMeet / Election Commission of India)
- Semantics: Generic ADM boundaries, claimed-extent India; carries NO Census 2011 geocodes, so it cannot satisfy the Census join (D3). Usable only as evaluation zones.
- Mechanism: HTTPS GET public GitHub raw file

### india-geoboundaries-adm2
- Title: geoBoundaries gbOpen IND ADM2 (DIAGNOSTIC ONLY – not Census 2011 codes)
- URL: https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM2/geoBoundaries-IND-ADM2.geojson
- Landing page: https://www.geoboundaries.org/
- Edition: geoBoundaries gbOpen, commit 9469f09 (same pin as Agent 2); 735 units expected
- Licence: ODbL 1.0 (Pathways Data / LGD)
- Semantics: Generic ADM boundaries, claimed-extent India; carries NO Census 2011 geocodes, so it cannot satisfy the Census join (D3). Usable only as evaluation zones.
- Mechanism: HTTPS GET public GitHub raw file

### india-census-2011-pca
- Title: Census of India 2011 Primary Census Abstract (town/village)
- URL: https://censusindia.gov.in/census.website/data/census-tables
- Landing page: https://www.censusindia.gov.in/census.website/en/data/population-finder
- Edition: Census of India 2011 PCA (state/district/sub-district/village-town level)
- Licence: Government of India open data; terms on censusindia.gov.in (verify per table before redistribution). Commit acquisition steps + checksums only.
- Semantics: Census COUNTS with state/district/sub-district/town-village codes. Not a raster.
- Mechanism: Manual/portal download of state-wise PCA tables (human step; no scripted bypass).
- Notes: Local copy not yet supplied; see BLOCKER_REQUEST.md item B2.

### india-admin-boundaries-2011
- Title: India 2011 administrative boundaries aligned to Census codes
- URL: (authoritative source to be confirmed by human; see BLOCKER_REQUEST.md B3)
- Landing page: https://bhuvan.nrsc.gov.in/ or https://onlinemaps.surveyofindia.gov.in/
- Edition: unconfirmed
- Licence: UNCONFIRMED – Survey of India / Census restrictions apply; do not substitute silently
- Semantics: Polygons carrying 2011 Census state/district/town/village geocodes; join integrity must be tested.
- Mechanism: Not yet determined. Candidate open sources listed in BLOCKER_REQUEST.md.

### india-bhuvan-lulc-50k-2011-12
- Title: Bhuvan LULC 1:50,000, 2011-12 (shapefile, by AOI/district)
- URL: https://bhuvan-app1.nrsc.gov.in/thematic/thematic/index.php
- Landing page: https://bhuvan.nrsc.gov.in/updates/bhuvan_apr2026.html
- Edition: NRSC LULC 50K, agricultural year 2011-12 (Bhuvan update 2026-04-16)
- Licence: National Geospatial Policy-2022 access; per-request approval, terms set by NRSC/Bhuvan
- Semantics: Categorical VECTOR LULC polygons at 1:50,000 map scale. Regridding to 23.5 m adds no detail. Use nearest-neighbour/rasterise-by-class, never averaging.
- Mechanism: Portal request (<=5 districts or AOI, purpose statement); link emailed after approval.
- Notes: Human action required – see BLOCKER_REQUEST.md B1. 2011-12 is a different period from Census 2011; document.

