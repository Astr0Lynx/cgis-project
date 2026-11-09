# Agent 3 handoff — 2011 selected-settlement Level 2

**Status: PARTIAL.** Level 2 implementation and synthetic verification are complete. Real-data acceptance is blocked on the sources listed below.

- Branch `completion/agent3-level2` in a dedicated worktree. Frozen base `c4dac59433f7aed90b8223ef6ba00cf4fef6449b`.
- Synthetic production code `075c7e454935a2317bd9ca97bc7236327a7d6afd`. Container packaging fix `77c23fce59319e17f3c7dac14b693965c16bbf85`. The handoff tip is the local branch SHA from `git rev-parse completion/agent3-level2`. The handoff and fixture files are layered above the tested code.
- Ownership covers `science/level2/**`. The branch starts directly from the frozen base. Agent 2 was inspected and tested in a separate detached checkout at `996d71a1cfe6bb4f3cca4129f88042e4494ccd8d`.
- D-L2-01 records the user’s approval of the hard residential or settlement mask on 9 November 2026. The alternative soft model measures sensitivity. The proposed readiness cutoffs are 95% coverage for each source, to be frozen before the real run. The actual code-to-class mapping requires the delivered legend. D-L2-02 remains open for any claim that a settlement is within a specified agreement threshold. [Method and assumptions](METHOD.md)

## Real inputs and acceptance

| Gate | Status | Evidence and dependency |
| --- | --- | --- |
| D3 | BLOCKED | ORGI PCA codes and 2011 counts verified for ten candidates. Matching 2011 polygons and approved Bhuvan 2011–12 1:50K analytical files with legend and use terms are pending. |
| S4 | BLOCKED | Six primary candidates across five regions and four reserves are ranked. Final inclusion requires matched Census support, a verified morphology stratum and authorized LULC coverage. |
| S5 | BLOCKED | Per-town COG/manifest generation, checkpointing, hard-mask weighting and mass-balance tests passed with synthetic data. The authorized selected set and accepted national 2011 Level 1 TIFF are needed to produce real COGs. |
| S6 | BLOCKED | Native-support aggregation, Census checks, residual maps and sensitivity reports passed with synthetic data. Real comparisons and the resource pilot require the same delivered inputs as S5. |

[Candidate codes, district AOIs, PCA citations and source checksums](SELECTION.md) · [Access and Level 1 delivery requirements](BLOCKER_REQUEST.md)

The official population totals are evidence for candidate identification. The real run needs the actual matched geometries and the authorized analytical legend. The published Level 1 branch contains metadata and evidence for the accepted national product. The national 2011 TIFF has SHA-256 `8284c2877c49d7329ea6b6775448a265c2403722f24466bb1ae218bdd34d3488` per Agent 2's handoff and needs to be made available locally. Keep the source data and restricted derivatives in ignored local storage.

## Synthetic results

Agent 2’s synthetic 2011 Level 1 COG was rebuilt at its published SHA using Python 3.12 and the pinned rasterio/GDAL environment. The regenerated synthetic raster had SHA-256 `2fb46c70beb96121ea39f59826a5c88ab2959ab662e8541f9f9e99d8974c9185`. The Level 2 package uses that geographic prior and synthetic Census and LULC. The separate automated known-value tests use small explicitly controlled counts and polygons.

| Synthetic town | Fixture PCA | Stored Level 2 | Absolute mass error | Level 1 on Census polygon observed support | Matched area | MAE per native intersection | Soft/hard people moved |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixture Alpha | 12,500 | 12,500.000028119 | 0.000028119 | 24,232.458 | 99.63% | 12.660 | 3,474.661 |
| Fixture Beta | 3,250 | 3,249.999998398 | 0.000001602 | 8,524.238 | 99.54% | 2.006 | 975.457 |

The fixture controls were set independently of the Level 1 sums. The residuals illustrate the effect of the imposed total and the hard mask. They provide no estimate of real settlement accuracy.

- [Portable v1 fixture manifest](examples/synthetic/manifest.json) lists two synthetic COGs and their SHA-256 checksums. The COGs are 13,518 and 22,601 bytes. The [benchmark and artifact hashes](examples/synthetic/run.json) enumerate both complete output bundles.
- [Alpha results](examples/synthetic/towns/fixture-alpha/report.json) · [Alpha evaluation figure](examples/synthetic/towns/fixture-alpha/evaluation.png) · [Beta results](examples/synthetic/towns/fixture-beta/report.json) · [Beta evaluation figure](examples/synthetic/towns/fixture-beta/evaluation.png)
- On Windows 11 with 16.31 GB physical RAM, the cross-agent run took 2.42 s and peaked at 149,876,736 bytes RSS (143 MiB). GDAL 3.9.3. Repeating the command reused 2 of 2 town checkpoints and preserved the manifest bytes. A real pilot is pending.
- The Linux container reproduced both population COGs byte for byte with the same Level 1 input. With a 6 GiB container limit, the two-town run took 1.47 s and peaked at 182,456,320 bytes RSS (174 MiB). The Windows and Linux times are observations of the small synthetic run. [Container benchmark](examples/synthetic/docker-run.json)

## Verification and reproduction

From the repository root with Python 3.12 and the dependencies in [requirements-dev.txt](requirements-dev.txt):

```sh
python -m pytest -q science/level2/tests
python -m ingestion.cli validate --manifest science/level2/examples/synthetic/manifest.json
```

- Automated tests **21 passed on Windows and 21 passed in Linux Docker**, covering known-value hard/soft allocation, nearest-neighbour, vector LULC, fractional areas, neighboring polygons and holes, no eligible cells, zero prior, NoData, Census join failures, grid errors, checkpoint integrity, tiling and v1/COG validation. [Container test log](examples/synthetic/pytest-container.txt)
- The unmodified importer validated **2 of 2** synthetic files. The repository's disposable Compose PostGIS test imported **2 of 2**, then returned **2 unchanged**. Two catalog rows were ready with the expected units, group, CRS and checksum. [Validation](examples/synthetic/ingestion-validate.jsonl) · [Import](examples/synthetic/catalog-import.jsonl) · [Import retry](examples/synthetic/catalog-retry.jsonl) · [Catalog rows](examples/synthetic/catalog-rows.txt)

To regenerate the cross-agent fixture after fetching Agent 2's branch, create a detached reference worktree and generate the 2011 synthetic result. The Windows run used the Level 2 environment plus SciPy 1.15.3 for the Agent 2 code.

```sh
git worktree add --detach data/work/agent2-checkout 996d71a1cfe6bb4f3cca4129f88042e4494ccd8d
(
  cd data/work/agent2-checkout
  export PYTHONPATH=science/level1
  python -m cgis_level1 make-synthetic --out data/level1-synthetic/inputs
  python -m cgis_level1 run --config science/level1/config/synthetic.toml --years 2011
)
python -m science.level2.cgis_level2 make-synthetic --out data/work/level2-demo \
  --level1-manifest data/work/agent2-checkout/data/level1-synthetic/outputs/manifest.json
python -m science.level2.cgis_level2 run --config data/work/level2-demo/config.json
python -m ingestion.cli validate --manifest data/work/level2-demo/outputs/manifest.json
```

The container uses the [pinned build command](README.md#container-and-checkpoints). Use the same fixture-generation and run arguments with `docker run --rm --memory=6g -v "$PWD/data:/repo/data" cgis-level2`. To run the suite in that image:

```sh
docker run --rm --memory=6g --entrypoint sh cgis-level2 -c \
  'python -m pip install pytest==8.4.2 && python -m pytest -q science/level2/tests'
```

For the disposable catalog check, initialize the environment and run the following from the worktree. Use a distinct Compose project so the test has its own database and asset volumes.

```sh
sh infra/scripts/init-env.sh
docker compose -p cgis-agent3-verify --profile tools build migrate ingestion
docker compose -p cgis-agent3-verify up -d --wait db
docker compose -p cgis-agent3-verify --profile tools run --rm \
  -v "$PWD/science/level2/examples/synthetic:/data/level2-demo:ro" ingestion \
  import --manifest /data/level2-demo/manifest.json
```

Run the import command a second time to check idempotence. The ready rows were queried from `catalog.current_datasets`.

## Integration

Agent 1 can use the documented AOIs and code join to deliver the authorized inputs. Agent 4 can ingest the manifest with the existing pipeline. Agent 6 can present the distinct town collections together using the real `india-level2-2011` group and can use the packaged synthetic group to test discovery. Agent 7 should verify the real data approvals and mass-balance outputs after merging the owner branches. [Agent 1 input handoff](docs/change-requests/agent3-to-agent1-inputs.md) · [Agent 4/6 grouping handoff](docs/change-requests/agent3-to-agent4-agent6-grouping.md)
