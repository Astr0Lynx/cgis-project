"""Write data/incoming/bench/manifest.json for the national benchmark (run from the repository root).

Always includes the SYNTHETIC national-grid raster; adds the REAL open datasets when their files exist:
  data/incoming/real/ind_ppp_2011.tif                (WorldPop, CC BY 4.0, doi:10.5258/SOTON/WP00645)
  data/incoming/real/geoBoundaries-IND-ADM2.geojson  (geoBoundaries gbOpen, CC BY 4.0, commit 9469f09)
Agent 1 owns the authoritative source inventory; these entries exist for platform testing.
"""

import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "."
out = os.path.join(root, "data/incoming/bench/manifest.json")
ds = [{
    "dataset_id": "synthetic-national-grid-2011", "version": 1, "kind": "raster", "product_level": "level1",
    "title": "SYNTHETIC national-grid benchmark raster (Level 1 grid; not population data)",
    "description": "Deterministic synthetic values on the exact 3 arc-second India Level 1 grid, for platform "
                   "benchmarks only.",
    "year": 2011, "units": "people_per_cell", "source": "infra/bench/make_national_raster.py",
    "file": "/data/incoming/bench/synthetic_national_grid.tif", "provenance": {"synthetic": True},
}]
if os.path.exists(os.path.join(root, "data/incoming/real/ind_ppp_2011.tif")):
    ds.append({
        "dataset_id": "india-worldpop-unconstrained-2011", "version": 1, "kind": "raster", "product_level": "source",
        "title": "WorldPop unconstrained population count, India 2011 (100 m)",
        "description": "WorldPop Global1 2000-2020 unconstrained individual-country estimate ind_ppp_2011.",
        "year": 2011, "units": "people_per_cell", "source": "WorldPop (www.worldpop.org)",
        "file": "/data/incoming/real/ind_ppp_2011.tif",
        "provenance": {
            "synthetic": False, "license": "CC BY 4.0",
            "citation": "WorldPop (2018). Global 1km/100m Population, doi:10.5258/SOTON/WP00645",
            "url": "ftp://ftp.worldpop.org/GIS/Population/Global_2000_2020/2011/IND/ind_ppp_2011.tif",
            "note": "platform benchmark import (Agent 4); Agent 1 owns the authoritative inventory"},
    })
if os.path.exists(os.path.join(root, "data/incoming/real/geoBoundaries-IND-ADM2.geojson")):
    ds.append({
        "dataset_id": "india-geoboundaries-adm2", "version": 1, "kind": "vector", "product_level": "source",
        "title": "India districts (geoBoundaries gbOpen ADM2, commit 9469f09)", "year": None, "units": None,
        "source": "geoBoundaries gbOpen", "file": "/data/incoming/real/geoBoundaries-IND-ADM2.geojson",
        "id_property": "shapeID", "name_property": "shapeName", "level_property": "shapeType",
        "unit_id_prefix": "gb-ind-adm2-",
        "provenance": {"synthetic": False, "license": "CC BY 4.0",
                       "citation": "Runfola et al. (2020) geoBoundaries. PLoS ONE 15(4): e0231866",
                       "url": "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM2/"},
    })
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"manifest_version": 1, "datasets": ds}, fh, indent=2)
print("manifest:", [d["dataset_id"] for d in ds])
