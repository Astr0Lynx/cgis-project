# Reproduction runbook (Agent 01)

Python ≥3.11 with `rasterio, numpy, pyproj, shapely, requests, psutil`; tests need `pytest`. Data go to the git-ignored `data/` tree:
`data/incoming` (originals, never edited) → `data/work` → `data/outputs/<delivery>`. Disk ≥ 40 GB free recommended (24 GB incoming + outputs).

```sh
cd science/data && export PYTHONPATH=.
python -m pytest -q                                       # 49 tests, synthetic fixtures only
python -m cgis_data probe                                  # HEAD all public URLs, record sizes
python -m cgis_data fetch india-worldpop-unconstrained-2011    # resumable where the server allows; ~1.8 GB each
#   faster, resumable alternative for WorldPop: ftp://ftp.worldpop.org/GIS/Population/Global_2000_2020/<YYYY>/IND/ind_ppp_<YYYY>.tif
#   -> verify size == Content-Length, place at data/incoming/<source_id>/ind_ppp_<YYYY>.tif, then re-run `fetch` to record sha256
python -m cgis_data tiles india-ghsl-built-total-2000 ... india-ghsl-pop-2015 --india   # 12 products x 14 tiles (2 sea tiles 404 = recorded absent)
python -m cgis_data fetch india-geoboundaries-adm1 india-geoboundaries-adm2             # diagnostic zones only
python -m cgis_data national                               # -> science/inputs/national_validation.json (~11 min, ~1 GB RSS)
python -m cgis_data grids                                  # per-year shape / lattice offsets
python -m cgis_data inventory                              # -> science/docs/source_inventory.json + SOURCE_INVENTORY.md
```
Regional sample + ingestion (repo root; needs Docker for a disposable PostGIS):
```sh
# build: cgis_data.sample.build_sample(root, state, "hyderabad-region", (77.9,16.9,79.1,17.9), 2011, [(8,26)])
#        cgis_data.vector_sample.make_vector_sample(...)  (see DATA_READINESS_REPORT.md)
python -m ingestion.cli validate --manifest data/outputs/hyderabad-region/manifest.json
docker run -d --name pg -e POSTGRES_PASSWORD=x -e POSTGRES_DB=cgis -p 127.0.0.1:55433:5432 postgis/postgis:17-3.5@sha256:01a6a70e41e6c4467c8f55f6063555ed72db2d6662cd0d571040d42eadaeb6f6
(cd database && CGIS_DATABASE_URL=postgresql://postgres:x@127.0.0.1:55433/cgis python -m cgis_db migrate up)
python -m ingestion.cli import --manifest data/outputs/hyderabad-region/manifest.json --assets-dir data/assets --dsn postgresql://postgres:x@127.0.0.1:55433/cgis
```
Gated sources (Census, Census-coded boundaries, Bhuvan LULC) are refused by `fetch`; follow `BLOCKER_REQUEST.md`.
