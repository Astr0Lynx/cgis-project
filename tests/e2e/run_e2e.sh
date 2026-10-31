#!/bin/sh
# Full end-to-end acceptance run against the Compose stack in this checkout.
#
#   sh tests/e2e/run_e2e.sh [--browser]
#
# --browser also drives the diagnostic viewer in headless Chromium (Playwright container, ~2 GB
# image, needs internet for the pinned MapLibre CDN script): tests/e2e/browser/check_viewer.py.
#
# Sequence: build + start (wait for health) -> seed twice -> negative imports with the real
# ingestion CLI -> pytest suite through the gateway -> gdalinfo on the exported clip ->
# restart every service (volumes kept) -> persistence tests -> status, logs and secret scan.
# It never deletes volumes. For a from-scratch run use a fresh clone (docs/integration/RUNBOOK.md).
# Evidence goes to ./data/fixtures/e2e/ (git-ignored). Exit status is non-zero if any step failed.

set -u
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1          # Git Bash: do not rewrite /data/... arguments into Windows paths

C="docker compose"
E="docker compose -f compose.yaml -f tests/e2e/compose.e2e.yaml"
EV=data/fixtures/e2e
FAILED=""

step() { printf '\n== [%s] %s\n' "$(date +%T)" "$*"; }
check() {  # check <name> <exit status>
    if [ "$2" -eq 0 ]; then echo "STEP PASS  $1"; else echo "STEP FAIL  $1 (exit $2)"; FAILED="$FAILED $1"; fi
    printf '%s %s\n' "$1" "$2" >> "$EV/steps.txt"
}

[ -f .env ] || { echo "missing .env: run sh infra/scripts/init-env.sh"; exit 2; }
step "prepare evidence directory $EV"
rm -rf "$EV" && mkdir -p "$EV/neg"
: > "$EV/steps.txt"
{ git rev-parse HEAD; git status --short | head -20; docker version --format 'docker {{.Server.Version}}'; $C version; } > "$EV/environment.txt" 2>&1

step "build all images (incl. ingestion tool) and start the stack, waiting for health"
start=$(date +%s)
# `up --build` skips the tool-profile ingestion image, so build every image explicitly first.
$C --profile tools build </dev/null > "$EV/build.log" 2>&1 && $C up -d --wait </dev/null > "$EV/up.log" 2>&1
check compose_up $?
echo "startup_seconds $(( $(date +%s) - start ))" >> "$EV/steps.txt"
$C ps -a > "$EV/compose-ps-initial.txt" 2>&1

step "seed SYNTHETIC fixtures (run 1)"
$C run --rm -T ingestion seed </dev/null > "$EV/seed1.jsonl" 2> "$EV/seed1.stderr"
check seed_1 $?
step "seed again (run 2, must be idempotent)"
$C run --rm -T ingestion seed </dev/null > "$EV/seed2.jsonl" 2> "$EV/seed2.stderr"
check seed_2 $?
grep -o '"dataset_id": "[^"]*"\|"outcome": "[^"]*"' "$EV/seed2.jsonl" | paste - - || true

step "build e2e runner and write negative import inputs"
$E build e2e </dev/null > "$EV/e2e-build.log" 2>&1
check e2e_build $?
$E run --rm -T --entrypoint python e2e make_negative_fixtures.py /data/fixtures/e2e/neg </dev/null > "$EV/neg/make.log" 2>&1
check negative_inputs $?

step "negative imports (each must fail without touching ready datasets)"
for case in corrupt_tiff missing_crs unsupported_crs conflicting_reimport invalid_geometry missing_file; do
    $C run --rm -T ingestion import --manifest "/data/fixtures/e2e/neg/$case.json" </dev/null \
        > "$EV/neg/$case.stdout" 2> "$EV/neg/$case.stderr"
    echo $? > "$EV/neg/$case.exit"
    printf '  %-22s exit %s  %s\n' "$case" "$(cat "$EV/neg/$case.exit")" \
        "$(grep -o '"code": "[^"]*"' "$EV/neg/$case.stdout" | head -1)"
done

step "host-level checks"
db_port=$($C port db 5432 2>/dev/null || true)
[ "$db_port" = ":0" ] && db_port=""      # compose prints ":0" when nothing is published
printf '{"db_published_port": "%s"}\n' "$db_port" > "$EV/host_checks.json"
cat "$EV/host_checks.json"

step "pytest: end-to-end suite through the gateway"
$E run --rm -T e2e -ra -m "not persistence" --junitxml=/data/fixtures/e2e/junit-main.xml </dev/null     > "$EV/pytest-main.txt" 2>&1
rc=$?
tail -40 "$EV/pytest-main.txt"
check pytest_main $rc

step "gdalinfo on the exported Town A clip"
if [ -f "$EV/clip_town_a_2001.tif" ]; then
    if command -v gdalinfo >/dev/null 2>&1; then
        gdalinfo -stats "$EV/clip_town_a_2001.tif" > "$EV/gdalinfo-clip.txt" 2>&1
    else
        docker run --rm -v "$(pwd)/$EV:/e2e:ro" ghcr.io/osgeo/gdal:alpine-small-3.10.3 \
            gdalinfo -stats /e2e/clip_town_a_2001.tif > "$EV/gdalinfo-clip.txt" 2>&1
    fi
    check gdalinfo $?
    rm -f "$EV/clip_town_a_2001.tif.aux.xml"
    grep -E 'Driver|Size is|EPSG|Origin|Pixel Size|NoData|Minimum=|STATISTICS_(MEAN|VALID)' "$EV/gdalinfo-clip.txt" | head -12
else
    check gdalinfo 1
fi

case " $* " in *" --browser "*)
    step "browser: drive the diagnostic viewer in headless Chromium"
    mkdir -p "$EV/browser"
    docker run --rm --network "$(docker compose ps -q gateway | xargs docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')"         --ipc=host -v "$(pwd)/tests/e2e/browser:/check:ro" -v "$(pwd)/$EV/browser:/out"         mcr.microsoft.com/playwright/python:v1.49.1-noble@sha256:42e251ec3238672c931be6c3565ff279690ae072c3e22058128d0412494f6866         sh -c 'python -c "import playwright" 2>/dev/null || pip install -q playwright==1.49.1 >/dev/null 2>&1; python /check/check_viewer.py http://gateway:8080/viewer/ /out'         </dev/null > "$EV/browser/check.txt" 2>&1
    rc=$?
    grep -E '^(PASS|FAIL|RESULT)' "$EV/browser/check.txt"
    check browser_viewer $rc
    ;;
esac

step "restart every service (volumes kept) and wait for health"
$C restart </dev/null > "$EV/restart.log" 2>&1 && $C up -d --wait </dev/null >> "$EV/restart.log" 2>&1
check restart $?

step "pytest: persistence after restart"
$E run --rm -T e2e -ra -m persistence --junitxml=/data/fixtures/e2e/junit-persistence.xml </dev/null     > "$EV/pytest-persistence.txt" 2>&1
rc=$?
tail -15 "$EV/pytest-persistence.txt"
check pytest_persistence $rc

step "status, logs and secret scan"
$C ps -a > "$EV/compose-ps.txt" 2>&1
$C ps -a --format '{{.Service}} {{.State}} {{.Status}}' > "$EV/compose-health.txt" 2>&1
cat "$EV/compose-health.txt"
$C logs --no-color --timestamps > "$EV/compose-logs.txt" 2>&1
leaks=0
for var in POSTGRES_PASSWORD INGEST_DB_PASSWORD OGC_DB_PASSWORD PROCESSING_DB_PASSWORD; do
    value=$(grep -E "^$var=" .env | head -1 | cut -d= -f2-)
    [ -n "$value" ] || continue
    if grep -R -F -q -- "$value" "$EV" --exclude=compose-logs.txt 2>/dev/null || grep -F -q -- "$value" "$EV/compose-logs.txt" \
       || git grep -F -q -- "$value" 2>/dev/null; then
        echo "LEAK: value of $var found in logs, evidence or tracked files"; leaks=$((leaks + 1))
    fi
done
check no_secret_leaks "$leaks"
for svc in db migrate tiles ogc processing gateway viewer; do
    n=$($C logs --no-color "$svc" 2>/dev/null | grep -c -E 'Traceback|CRITICAL|FATAL|\[emerg\]' || true)
    echo "log_errors $svc $n" >> "$EV/steps.txt"
done
# pygeoapi logs every 4xx client error (from the negative tests) with a traceback, and the restart
# step makes PostgreSQL log FATAL "terminating connection"; neither is a server fault. Server
# faults show up as 5xx responses in the gateway's JSON access log:
n5xx=$($C logs --no-color gateway 2>/dev/null | grep -c -E '"status":5[0-9][0-9]' || true)
echo "gateway_5xx_responses $n5xx" >> "$EV/steps.txt"
grep -E '^(log_errors|gateway_5xx)' "$EV/steps.txt"
check no_server_errors "$n5xx"

step "summary"
cat "$EV/steps.txt"
if [ -z "$FAILED" ]; then
    echo "E2E RESULT: PASS"
    exit 0
fi
echo "E2E RESULT: FAIL ->$FAILED"
exit 1
