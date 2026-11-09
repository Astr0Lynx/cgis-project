# CyberGIS Level 2 selected-town refinement

Produces a Census-constrained 2011 population COG on a 23.5 m equal-area target grid for each selected settlement. The run includes a v1 ingestion manifest, native Level 1 comparison GeoTIFF, evaluation figure, per-town JSON and resource evidence. See [the method](METHOD.md), [candidate selection](SELECTION.md) and [real-data requirements](BLOCKER_REQUEST.md).

## Run the software against synthetic inputs

Use Python 3.12 from the repository root. Data paths below are ignored by Git.

```sh
python -m pip install -r science/level2/requirements-dev.txt
python -m pytest -q science/level2/tests
python -m science.level2.cgis_level2 make-synthetic --out data/work/level2-demo
python -m science.level2.cgis_level2 run --config data/work/level2-demo/config.json
python -m ingestion.cli validate --manifest data/work/level2-demo/outputs/manifest.json
```

Use a new empty fixture directory. Rerun only the `run` command to exercise checkpoint reuse. To use Agent 2's synthetic 2011 product, add `--level1-manifest <local Level 1 synthetic manifest>` to `make-synthetic`. It expects the v1 `synthetic-india-l1-pop-2011@v1` entry and verifies the TIFF checksum. It requires the generated asset beside the manifest at its manifest-relative path.

For a ready-to-ingest sample, use [the packaged synthetic manifest and two COGs](examples/synthetic/manifest.json).

If another GIS installation sets global PROJ paths on Windows, clear them in the current PowerShell session before using the pinned wheels. Run `Remove-Item Env:PROJ_LIB,Env:PROJ_DATA -ErrorAction SilentlyContinue`. Install with `py -3.12` or the Python 3.12 environment of your choice.

## Run with authorized inputs

1. Copy [the config template](config/selected.example.json) to `data/incoming/level2/config.json`. Paths are relative to that file unless absolute. Place the accepted Level 1 manifest and its TIFF in the location configured under `sources.level1`.
2. Write `selected.csv` from the frozen candidate audit. Include `census_id,slug,name,region,morphology,population_2011,status,rationale`. Use `selected` for eligible rows. Use only matching Census totals in `pca.csv` with `census_id,name,population_2011`. Prepare a WGS84 RFC 7946 `study_areas.geojson` for exactly the selected IDs. Each Polygon/MultiPolygon requires `properties.census_id`, `properties.name` and numeric `properties.census_year: 2011`.
3. Convert the authorized shapefiles to indexed GeoPackages (or small GeoJSONs) with the original CRS and exact category field. For instance, `ogr2ogr -f GPKG local.gpkg approved.shp -nln lulc`. Document checksums of the original delivery and the preparation steps. If clipping before delivery, cover the entire selected polygon with a buffer of at least one native Level 1 cell. A prepared categorical raster may be used by setting `kind` to `raster` and supplying a north-up single integer band with declared NoData. Preserve the class codes and use nearest-neighbour for any upstream categorical reprojection.
4. Write the normalized `legend.json` as `{"classes":[{"code":<integer>,"label":<official class label>,"source_value":<verbatim source field as a string>},...]}`. The normalized integer code is used for raster input and internal vector rasterization. Map each exact legend label in `method.class_weights` to a sensitivity weight (`soft`, between 0 and 1) and a primary eligibility flag (`hard`, Boolean). Record verified code, use/scale and accuracy evidence, source SHA-256, license and nonsecret access references. Verify the exact mapping against D-L2-01 and resolve material ambiguities before setting `approval_status` to `approved`. Record the policy approval and mapping evidence in `method.approval_reference`. Real mode requires the hard mask, authorized inputs, clean committed Level 2 code, and at least six selected settlements across four regions and three morphology types.
5. Run `python -m science.level2.cgis_level2 run --config data/incoming/level2/config.json --only <selected-town-slug>` with the other heavy services stopped. This profiles a representative town and writes a pilot report and checkpoint. Review coverage, mass balance, elapsed time, disk use and RSS before repeating the command without `--only`. The full run reuses the pilot checkpoint and publishes the manifest. Validate `data/outputs/level2/manifest.json` with the existing ingestion CLI and share only the artifacts permitted by the source terms.

## Container and checkpoints

From Git Bash at a clean committed revision, use an archive for an immutable build. Docker Desktop needs access to the local workspace volume.

```sh
git archive --format=tar HEAD | docker build -f science/level2/Dockerfile \
  --build-arg MODEL_COMMIT="$(git rev-parse HEAD)" -t cgis-level2 -
docker run --rm --memory=6g -v "$PWD/data:/repo/data" cgis-level2 \
  run --config data/incoming/level2/config.json
```

For synthetic container verification, mount the self-contained synthetic input directory as part of `data/`. The optional Agent 2 manifest path must also resolve inside the container. Absolute Windows paths in a native configuration cannot be resolved inside Linux.

The calculation processes towns sequentially, evaluates one bounded native Level 1 window per town, and works in 256 × 256 fine blocks. Use self-contained TIFFs with internal NoData or masks so the source checksum covers all raster data used. Capacity limits are 25 million fine pixels and two million native pixels per town, 100 selected towns, and a limit of 100,000 vector LULC features or two million vertices per settlement read. The process checks its peak resident memory between blocks. Configure a per-process budget (4 GiB proposed) in addition to a container memory limit. The CLI records elapsed time, OS peak RSS and artifact sizes. Scale tests with authorized real data are required before interpreting these limits as a benchmark.

Per-town results are stored atomically in a checksummed folder under `outputs/towns/`. A retry reuses complete, verified towns for the same config, input checksums and model. An incomplete town is recomputed. Corrupt checkpoints are reported with their folder name. Use a fresh output directory when a checkpoint is corrupt. Keep input files immutable during a run and use separate output directories for parallel configs. Increment the dataset version whenever population TIFF content changes before catalog publication. The manifest is written after all configured towns succeed.

## Agent 4 and Agent 6 integration

Run `ingestion.cli validate` or Compose `ingestion validate` on the generated v1 manifest, then use the existing versioned importer and schema. Real IDs are `india-l2-pop-2011-<town-slug>` with `provenance.group_id=india-level2-2011`. The fixture uses distinct IDs and `synthetic-india-level2-2011`. Keep each town as a separate collection. Group in discovery and compare only on matched support. Native units are people per cell. Treat overviews as display-only and use the local evaluation rasters for quantitative comparisons. [Grouping handoff](docs/change-requests/agent3-to-agent4-agent6-grouping.md)
