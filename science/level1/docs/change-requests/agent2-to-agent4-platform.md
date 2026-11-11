# Change request: Agent 2 → Agent 4 (science compute profile; national raster import)

**Status:** open. **Requested by:** Agent 2. **Owner:** Agent 4 (`compose.yaml`, `infra/`, `ingestion/`).
Agent 2 has **not** edited any Agent 4 path.

## 1. Optional `science` Compose profile (no change to default services)

Proposed addition to `compose.yaml` (profile only, so a plain `docker compose up` is unchanged):

```yaml
  level1:
    profiles: ["science"]
    build: {context: ., dockerfile: science/level1/Dockerfile}
    volumes: ["./data:/repo/data"]
    mem_limit: 14g          # leaves headroom on the 16 GB target; the pipeline also self-limits (run.max_rss_gb=12)
    command: ["run", "--config", "science/level1/config/india_full.toml"]
```

Run it with other heavy services stopped:
`docker compose --profile science run --rm level1 run --config science/level1/config/india_full.toml`.
The Dockerfile uses `science/level1/Dockerfile.dockerignore`, so `data/` is never sent as build context.

## 2. National Level 1 import: facts for capacity planning

Each `india-l1-pop-YYYY` is float32, 35075 × 34497 (1.21·10⁹ cells), EPSG:4326. COG sizes are measured
in `benchmarks/`. Observations from running the **unchanged** importer's validation on these files:

* `ingestion validate` / `import` read every pixel (by design). Allow minutes per year. The block-wise code
  stays memory-bounded. Measured times are in `HANDOFF_REPORT.md`.
* The manifest supplies `expect.bounds/resolution/width/height/crs/nodata/dtype` and `checksum_sha256`.
  Please keep these semantics stable.
* Overviews in our COGs are NEAREST (display only), consistent with the importer.

## 3. Requests

* Confirm that 11 national float32 COGs (~X GB total, see benchmarks) fit the assets volume, and that
  TiTiler/OGC Tiles legends can use a shared scale across years (Level 1 values are people per ~100 m
  cell, typically 0–2 000, with a heavy tail in dense urban cells).
* Keep `product_level=level1` and `units=people_per_cell` as-is. `spatial-processing` already treats
  `people_per_cell` as a count, so zonal **sum** works.

## 4. Defect found: `ingestion.importer._fsync_file` fails on Windows hosts (portability, low severity)

`ingestion/importer.py:265-267`:

```python
def _fsync_file(path):
    with open(path, "rb") as fh:
        os.fsync(fh.fileno())
```

`os.fsync` on a **read-only** file descriptor works on Linux but raises `OSError: [Errno 9] Bad file descriptor`
on Windows (the C runtime `_commit` needs a write handle). Every raster import then fails with
`internal_error` after the COG has been written. Observed running the unchanged importer natively on Windows 11
for `india-l1-pop-2001` and `-2002`. Whether the Linux container import succeeds is recorded in HANDOFF_REPORT (section 5).

* **Impact:** expected none for Docker/Compose (the project's target, Linux; to be confirmed by the Linux import
  result in HANDOFF_REPORT); breaks native Windows runs of the importer.
* **Proposed fix (Agent 4's path, not changed by Agent 2):** open the file for update, e.g.
  `with open(path, "r+b") as fh: os.fsync(fh.fileno())`, or catch `OSError` with `errno.EBADF` on `os.name == "nt"`.
* **Backward compatibility:** none affected. **Test:** import any fixture on a Windows host, or unit-test with a
  read-only temp file.
* **How Agent 2 worked around it:** ran the unchanged importer inside the `cgis-level1` Linux container (see HANDOFF_REPORT).
