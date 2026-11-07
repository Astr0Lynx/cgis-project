# `science/level1`: India-wide Level 1 population model, 2001–2011 (Agent 2)

Implements page 1 of the original flowchart. WorldPop unconstrained counts are redistributed within
~1 km parents in proportion to GHSL **residential** built-up surface (total − non-residential), with
temporal interpolation of the GHSL epochs to each year. The result is eleven ~100 m `people_per_cell`
COGs, `india-l1-pop-2001` … `india-l1-pop-2011`. The model is evaluated against GHS-POP.

* Method of record: [`METHOD.md`](METHOD.md)
* Human decisions (2011 policy, parent support): [`METHODOLOGY_DECISION.md`](METHODOLOGY_DECISION.md)
* Evaluation results and figures: [`eval/`](eval/)
* Benchmarks (peak RSS, time, disk): [`benchmarks/`](benchmarks/)
* Handoff with SHAs, checksums and status: [`HANDOFF_REPORT.md`](HANDOFF_REPORT.md)

## Layout

```
cgis_level1/   core.py (pure maths) · grid.py (target grid, GHSL mosaic, alignment) · pipeline.py (tiled,
               checkpointed run + COG + manifest) · evaluate.py / figures.py (GHS-POP evaluation) ·
               inventory.py (input checksums) · synthetic.py (SYNTHETIC fixtures) · monitor.py (RSS budget)
config/        india_full.toml · india_sample_delhi.toml · synthetic.toml   (deterministic TOML)
scripts/       fetch_inputs.sh (lawful open-data fallback acquisition) · run_all.sh
tests/         golden tiny rasters, seams/restart/idempotency, ingestion-contract, negative cases
examples/      stable SYNTHETIC Level 1 manifest for Agents 3/4/6
eval/          per-year evaluation bundles (JSON) + figures (PNG), policy_2011_comparison.json
benchmarks/    measured resource use (real runs)
provenance/    input inventory (sizes + SHA-256), licences
docs/change-requests/   requests to other owners (Agent 1, Agent 4)
```

Large rasters are **never committed**. Inputs go in `data/incoming/`, checkpoints in `data/work/level1/`
and outputs in `data/outputs/level1/`. All of these are under the git-ignored `/data/`.

## Requirements

Python 3.12 with `pip install -r science/level1/requirements-dev.txt` (rasterio 1.5 wheels bundle
GDAL/PROJ), or the container:

```sh
docker build -f science/level1/Dockerfile -t cgis-level1 .          # from the repository root
docker run --rm --memory=16g -v "$PWD/data:/repo/data" cgis-level1 run --config science/level1/config/india_full.toml
```

On Windows hosts that also have PostGIS installed, a foreign `proj.db` can shadow rasterio's.
`cgis_level1` always points PROJ at rasterio's bundled database.

## 1. Tests (synthetic, ~15 s)

```sh
cd science/level1 && python -m pytest -q
```

## 2. Synthetic end-to-end (proves the software, not the science)

```sh
export PYTHONPATH=science/level1
python -m cgis_level1 make-synthetic --out data/level1-synthetic/inputs
python -m cgis_level1 run      --config science/level1/config/synthetic.toml
python -m cgis_level1 evaluate --config science/level1/config/synthetic.toml
python -m ingestion.cli validate --manifest data/level1-synthetic/outputs/manifest.json   # repo's own validator
```

## 3. Real inputs (open data, CC BY 4.0)

Agent 1 owns acquisition. Its deliveries were not available on the frozen base, so the open inputs were
fetched as a **documented lawful fallback** (no access controls involved):

```sh
science/level1/scripts/fetch_inputs.sh data/incoming        # resumable; ~20.3 GB WorldPop + ~3.5 GB GHSL
python -m cgis_level1 inventory --config science/level1/config/india_full.toml   # sizes + SHA-256 of every input
```

| Input | Files | Licence |
|---|---|---|
| WorldPop Global1 unconstrained India `ind_ppp_YYYY.tif`, 2001–2011 | 11 × ~1.85 GB | CC BY 4.0, doi:10.5258/SOTON/WP00645 |
| GHS-BUILT-S R2023A total + NRES, 4326_3ss, E2000/E2005/E2010 (+E2015, D-L1-01 option C) | 14 tiles each (R6–R9 × C25–C28, sea tiles absent) | CC BY 4.0, EC JRC |
| GHS-POP R2023A 4326_3ss, E2000/E2005/E2010/E2015 (evaluation only) | 14 tiles each | CC BY 4.0, EC JRC |
| geoBoundaries gbOpen IND ADM1 / ADM2 (diagnostic evaluation zones only) | 2 GeoJSON | CC BY 2.5 IN / ODbL 1.0 |

## 4. Real small sample (Delhi NCR, about 10 min for 11 years)

```sh
python -m cgis_level1 run      --config science/level1/config/india_sample_delhi.toml
python -m cgis_level1 evaluate --config science/level1/config/india_sample_delhi.toml
```

Sample pixels are bit-identical to the same pixels of the national product, because they use the same
parent lattice.

## 5. Full India, every year (restartable)

```sh
python -m cgis_level1 run      --config science/level1/config/india_full.toml     # all 11 years, one at a time
python -m cgis_level1 evaluate --config science/level1/config/india_full.toml
python -m cgis_level1 summary  --config science/level1/config/india_full.toml
python -m ingestion.cli validate --manifest data/outputs/level1/manifest.json
# catalog import (Agent 4 path, unchanged importer):
docker compose run --rm -v "$PWD/data/outputs/level1:/data/incoming/level1:ro" ingestion \
    import --manifest /data/incoming/level1/manifest.json
```

* An interrupted run can be repeated with the same command. Checksum-verified tile checkpoints are
  reused, corrupt or missing ones are recomputed, and completed years are skipped. `--force` recomputes.
* `--years 2011` runs a single year. `--max-tiles N` is for debugging only and is never reported as PASS.
* Disk: about 20.3 GB for WorldPop inputs, ~3.5 GB for GHSL, plus outputs and transient chunks. Measured
  values are in `benchmarks/`.

## Output contract (for Agents 3, 4, 6, 7)

| Field | Value |
|---|---|
| dataset_id | `india-l1-pop-YYYY`, YYYY = 2001…2011 (sample: `india-l1-sample-delhi-pop-YYYY`; synthetic: `synthetic-india-l1-pop-YYYY`) |
| product_level / units | `level1` / `people_per_cell` (counts. Never sum a density.) |
| grid | WorldPop native: EPSG:4326, 0.00083333333° (3″), origin 68.185416408°E 35.501250059°N, 35075 × 34497 |
| dtype / NoData | float32 / −99999 (sea and outside WorldPop coverage) |
| file | lossless COG (DEFLATE + predictor 3, 512 blocks, NEAREST overviews for display only) |
| manifest | `data/outputs/level1/manifest.json` (`manifest_version: 1`), per-year entries in `manifests/` |
| provenance | upstream SHA-256 (WorldPop + every GHSL tile), model commit, config SHA-256, temporal rule, 2011 policy and approval, mass balance |
| 2011 | `PROVISIONAL` until D-L1-01 is signed off (see `METHODOLOGY_DECISION.md`) |
