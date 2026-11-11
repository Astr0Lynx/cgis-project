#!/bin/sh
# National-scale platform benchmark on a SEPARATE Compose project (cgis-bench): the main stack and its
# volumes are never touched. Evidence: ./data/bench/ (git-ignored); the reviewed summary is copied to
# infra/docs/evidence/.
#
#   sh infra/bench/run_national_bench.sh
#
# Inputs: a SYNTHETIC raster on the exact national Level 1 grid (generated here, bounded memory), a
# SYNTHETIC 12 100-polygon boundary grid (SQL), and - when present - REAL open data that is never
# committed: data/incoming/real/ind_ppp_2011.tif (WorldPop) and geoBoundaries-IND-ADM2.geojson.
#
# Steps: build + up -> generate raster -> import every dataset with the real ingestion CLI while sampling
# `docker stats` -> HTTP/DB benchmark (national_bench.py) -> kill the processing service during an
# India-wide async job and check it is retried to completion -> asset volume size -> peak memory.
# Tear down: docker compose -p cgis-bench down -v
set -u
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1
[ -f .env ] || { echo "missing .env (sh infra/scripts/init-env.sh)"; exit 2; }
set -a; . ./.env; set +a
export COMPOSE_PROJECT_NAME=cgis-bench PUBLIC_BASE_URL=http://gateway:8080 OGC_REFRESH_INTERVAL=5 GATEWAY_PORT=18090
export COMPOSE_PROFILES=
C="docker compose -p cgis-bench"
NET=cgis-bench_default
EV=data/bench
IN=data/incoming/bench
T=cgis-ogc-tests:dev                     # python + rasterio + httpx + psycopg; runs every helper
mkdir -p "$EV" "$IN"
step() { printf '\n== [%s] %s\n' "$(date +%T)" "$*"; }
inpy() { docker run --rm --network "$NET" -v "$(pwd):/repo" -w /repo "$T" python "$@"; }

step "start the stack (project cgis-bench)"
$C --profile tools build </dev/null > "$EV/build.log" 2>&1 && $C up -d --wait </dev/null > "$EV/up.log" 2>&1 \
    || { echo "stack failed (see $EV/up.log)"; exit 1; }
docker build -q -t "$T" -f ogc-services/tests/Dockerfile ogc-services >/dev/null

rm -f "$EV/.stop-sampler"
( while [ ! -f "$EV/.stop-sampler" ]; do
      docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | sed "s/^/$(date +%s) /"
      sleep 1
  done ) > "$EV/docker-stats.txt" &
SAMPLER=$!

step "generate the SYNTHETIC national-grid raster (35075 x 34497 float32), 2 GiB container limit"
if [ ! -f "$IN/synthetic_national_grid.tif" ]; then
    docker run --rm --memory 2g -v "$(pwd):/repo" -w /repo "$T" \
        python infra/bench/make_national_raster.py "$IN/synthetic_national_grid.tif" > "$EV/make_raster.json" 2> "$EV/make_raster.err"
fi
cat "$EV/make_raster.json" 2>/dev/null; ls -l "$IN/synthetic_national_grid.tif"

step "write the import manifest"
inpy infra/bench/write_manifest.py /repo

step "import with the real ingestion CLI (validate every pixel, lossless COG, verify, register)"
start=$(date +%s)
$C run --rm -T ingestion import --manifest /data/incoming/bench/manifest.json </dev/null > "$EV/import.jsonl" 2> "$EV/import.stderr"
echo "import_exit $? seconds $(( $(date +%s) - start ))" | tee "$EV/import.time"
grep -o '"dataset_id": "[^"]*"\|"outcome": "[^"]*"' "$EV/import.jsonl" | paste - - || true

step "asset volume size"
docker run --rm -v cgis-bench_assets:/a busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec \
    du -sk /a/rasters /a/vectors 2>/dev/null | tee "$EV/assets_du.txt"

step "HTTP/DB benchmark through the gateway"
RASTERS="--raster synthetic-national-grid-2011"
grep -q '"dataset_id": "india-worldpop-unconstrained-2011"' "$EV/import.jsonl" && RASTERS="$RASTERS --raster india-worldpop-unconstrained-2011"
VECTORS=""
grep -q '"dataset_id": "india-geoboundaries-adm2"' "$EV/import.jsonl" && VECTORS="--vector india-geoboundaries-adm2"
docker run --rm --network "$NET" -e PGHOST=db -e PGUSER="$POSTGRES_USER" -e PGPASSWORD="$POSTGRES_PASSWORD" \
    -e PGDATABASE="$POSTGRES_DB" -v "$(pwd):/repo" -w /repo "$T" \
    python infra/bench/national_bench.py --gateway http://gateway:8080 $RASTERS $VECTORS --out "$EV/national_bench.json" \
    </dev/null > "$EV/national_bench.stdout" 2>&1
echo "bench_exit $?"; tail -3 "$EV/national_bench.stdout"

step "restart persistence: kill the processing service during an India-wide async job"
JOB=$(inpy -c "import httpx; r=httpx.post('http://gateway:8080/api/operations/zonal-statistics', json={'dataset_id':'synthetic-national-grid-2011','aoi':{'type':'Polygon','coordinates':[[[68.2,6.8],[97.4,6.8],[97.4,35.5],[68.2,35.5],[68.2,6.8]]]},'aoi_crs':'EPSG:4326','mode':'async'}); print(r.json()['job_id'])")
echo "job $JOB"
sleep 10
$C kill processing >/dev/null 2>&1
$C up -d --wait processing >/dev/null 2>&1
inpy -c "
import httpx, json, time
t0 = time.time()
while True:
    j = httpx.get('http://gateway:8080/api/operations/$JOB').json()
    if j['status'] in ('completed', 'failed') or time.time() - t0 > 1800: break
    time.sleep(3)
json.dump(j, open('$EV/restart_job.json', 'w'), indent=2, default=str)
print({k: j.get(k) for k in ('status', 'attempts', 'max_attempts', 'error')}, (j.get('result') or {}).get('count_valid'))
"

step "headless QGIS opens the downloaded national async clip(s)"
for f in "$EV"/clip_async_*.tif; do
    [ -f "$f" ] || continue
    docker run --rm -e QT_QPA_PLATFORM=offscreen -v "$(pwd):/repo:ro"         qgis/qgis:3.44.15@sha256:9c4afddd8eb4d03275e161b732d57ea402a1a5237eb5766957427530fd38afe0         python3 /repo/infra/bench/qgis_check_tif.py "/repo/$f" EPSG:4326 </dev/null 2>&1 | grep -E '^(PASS|FAIL|INFO|RESULT)'
done | tee "$EV/qgis_clip.txt"

touch "$EV/.stop-sampler"; wait "$SAMPLER" 2>/dev/null
step "peak memory per container (docker stats samples)"
inpy -c "
import re, collections
peak = collections.defaultdict(float)
unit = {'B': 1 / 2**20, 'KiB': 1 / 1024, 'MiB': 1, 'GiB': 1024}
for line in open('$EV/docker-stats.txt', encoding='utf-8', errors='replace'):
    m = re.match(r'\d+ (\S+) ([\d.]+)(B|KiB|MiB|GiB) /', line)
    if m:
        name = re.sub(r'-run-[0-9a-f]+$', '-run', m.group(1))
        peak[name] = max(peak[name], float(m.group(2)) * unit[m.group(3)])
for name, mib in sorted(peak.items()):
    print(f'{name:45s} peak {mib:8.1f} MiB')
" | tee "$EV/peak_memory.txt"
echo "done; evidence in $EV"
