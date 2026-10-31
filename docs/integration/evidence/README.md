# Evidence: final validation of the tested commit `d4b6cfc`

These are raw outputs from a **fresh clone** of `phase2/integration` @ `d4b6cfc` (Windows 11,
Docker Desktop 29.1.3 / Compose v5.0.0), with a no-cache image build. They were copied unchanged
from `./data/fixtures/e2e/` and `./data/modtest-evidence/`, except that image-pull progress lines
were removed from `cite/cite-summary.txt`. Every file was checked to contain no `.env` password.

| File | Produced by |
|---|---|
| `tested-commit.txt`, `environment.txt` | `run_e2e.sh` header: commit, Docker and Compose versions |
| `build-nocache.txt` | `docker compose --profile tools build --no-cache --pull` (time, exit) |
| `e2e-steps.txt` | `run_e2e.sh --browser --qgis`: every step with its exit status, start-up seconds, log and 5xx counts |
| `e2e-pytest-main.txt`, `junit-main.xml` | pytest, 102 tests (everything except persistence) |
| `e2e-pytest-persistence.txt`, `junit-persistence.xml` | pytest `-m persistence` after `down` + `up` |
| `seed-run1.jsonl`, `seed-run2.jsonl` | `docker compose run --rm ingestion seed` ×2 (imported, then unchanged) |
| `negative-imports/*.txt` | Exit status and CLI output for the six bad imports |
| `gdalinfo-clip.txt` | `gdalinfo -stats` on the Town A / 2001 clip (host GDAL) |
| `qgis-geotiff.txt` | Headless QGIS 3.44.15 opening the same clip |
| `viewer/` | Headless Chromium viewer check: JSON results and two screenshots |
| `perf.json` | Latencies (informational) |
| `compose-ps.txt`, `compose-health.txt`, `host_checks.json` | Status after the run; the DB has no published host port |
| `bare-restart.txt` | `docker compose restart` → `up --wait` → smoke test |
| `module-suites.txt` | `run_module_suites.sh --cite --qgis`: summary plus the tail of each owner's suite |
| `cite/` | OGC TEAM Engine results (Features 1.0 ETS 1.8, Tiles 1.0 ETS 1.1), summary and gzipped XML |
| `qgis-oapif-xyz.txt` | Agent 4's headless QGIS check (OGC Features and OGC Tiles as XYZ) |
| `gateway-routes-mock.txt` | `infra/tests/test_gateway_routes.sh` against mock upstreams (integration worktree, after the last gateway change) |
