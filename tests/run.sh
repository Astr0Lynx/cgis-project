#!/bin/sh
# Test runner for the whole stack (make e2e / make test-modules). Never deletes the main stack's volumes.
#
#   sh tests/run.sh e2e     [--qgis] [--browser]   # acceptance run through the gateway (project "cgis")
#   sh tests/run.sh modules [--qgis] [--cite]      # each module's own pytest suite on a throwaway stack
#
# e2e:     build + start -> seed twice -> negative imports -> pytest via gateway -> gdalinfo on the
#          exported clip -> restart (volumes kept) -> persistence tests -> logs and secret scan.
# modules: database, ingestion, ogc-services and spatial-processing suites against a separate
#          Compose project "cgis-modtest" (gateway port 18088). Tear down: docker compose -p cgis-modtest down -v
# Evidence: ./data/fixtures/e2e/ or ./data/modtest-evidence/ (git-ignored). Exit status is non-zero on any failure.

set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1          # Git Bash: do not rewrite /data/... arguments into Windows paths
[ -f .env ] || { echo "missing .env: run sh infra/scripts/init-env.sh"; exit 2; }
MODE="${1:-}"; [ $# -gt 0 ] && shift
QGIS_IMAGE=qgis/qgis:3.44.15@sha256:9c4afddd8eb4d03275e161b732d57ea402a1a5237eb5766957427530fd38afe0
PY_IMAGE=python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c
FAILED=""

# Print a timestamped section header. Used by every step below.
step() { printf '\n== [%s] %s\n' "$(date +%T)" "$*"; }

# Record one step result: check <name> <exit status>. Used by run_e2e and run_modules.
check() {
    if [ "$2" -eq 0 ]; then echo "PASS  $1"; else echo "FAIL  $1 (exit $2)"; FAILED="$FAILED $1"; fi
    printf '%s %s\n' "$1" "$2" >> "$EV/steps.txt"
}

# True when an optional flag was passed: has_flag --qgis "$@". Used by run_e2e and run_modules.
has_flag() { flag="$1"; shift; case " $* " in *" $flag "*) return 0 ;; esac; return 1; }

# Print the summary and exit with the overall result. Used at the end of both modes.
finish() {
    step "summary"
    cat "$EV/steps.txt"
    if [ -z "$FAILED" ]; then echo "RESULT: PASS"; exit 0; fi
    echo "RESULT: FAIL ->$FAILED"; exit 1
}

# Full acceptance run against the main stack in this checkout. Used by `run.sh e2e` (make e2e).
run_e2e() {
    C="docker compose"
    EV=data/fixtures/e2e
    rm -rf "$EV" && mkdir -p "$EV/neg" && : > "$EV/steps.txt"
    { git rev-parse HEAD; git status --short | head -20; $C version; } > "$EV/environment.txt" 2>&1

    step "build all images and start the stack, waiting for health"
    start=$(date +%s)
    # `up --build` skips profile images (ingestion, e2e), so build them explicitly.
    $C --profile tools --profile e2e build </dev/null > "$EV/build.log" 2>&1 && $C up -d --wait </dev/null > "$EV/up.log" 2>&1
    check compose_up $?
    echo "startup_seconds $(( $(date +%s) - start ))" >> "$EV/steps.txt"

    step "seed SYNTHETIC fixtures twice (second run must be idempotent)"
    $C run --rm -T ingestion seed </dev/null > "$EV/seed1.jsonl" 2> "$EV/seed1.stderr"; check seed_1 $?
    $C run --rm -T ingestion seed </dev/null > "$EV/seed2.jsonl" 2> "$EV/seed2.stderr"; check seed_2 $?

    step "negative imports (each must fail without touching ready datasets)"
    $C run --rm -T --entrypoint python e2e make_negative_fixtures.py /data/fixtures/e2e/neg </dev/null > "$EV/neg/make.log" 2>&1
    check negative_inputs $?
    for case in corrupt_tiff missing_crs unsupported_crs conflicting_reimport invalid_geometry missing_file; do
        $C run --rm -T ingestion import --manifest "/data/fixtures/e2e/neg/$case.json" </dev/null \
            > "$EV/neg/$case.stdout" 2> "$EV/neg/$case.stderr"
        echo $? > "$EV/neg/$case.exit"
        printf '  %-22s exit %s  %s\n' "$case" "$(cat "$EV/neg/$case.exit")" \
            "$(grep -o '"code": "[^"]*"' "$EV/neg/$case.stdout" | head -1)"
    done

    step "host-level checks"
    # Docker prints ":0" and podman "invalid IP:0" when nothing is published; keep only a real host:port.
    db_port=$($C port db 5432 2>/dev/null | grep -E '^[0-9.:\[\]a-f]+:[1-9][0-9]*$' || true)
    printf '{"db_published_port": "%s"}\n' "$db_port" > "$EV/host_checks.json"

    step "pytest: end-to-end suite through the gateway"
    $C run --rm -T e2e -m "not persistence" </dev/null > "$EV/pytest-main.txt" 2>&1
    rc=$?; tail -25 "$EV/pytest-main.txt"; check pytest_main $rc

    step "gdalinfo on the exported Town A clip"
    if [ -f "$EV/clip_town_a_2001.tif" ]; then
        docker run --rm -v "$(pwd)/$EV:/e2e:ro,z" ghcr.io/osgeo/gdal:alpine-small-3.10.3 \
            gdalinfo -stats /e2e/clip_town_a_2001.tif > "$EV/gdalinfo-clip.txt" 2>&1
        check gdalinfo $?
        grep -E 'Size is|EPSG|Origin|Pixel Size|NoData|Minimum=' "$EV/gdalinfo-clip.txt" | head -8
    else
        check gdalinfo 1
    fi

    if has_flag --qgis "$@"; then
        step "QGIS: open the exported clip in headless QGIS"
        docker run --rm -e QT_QPA_PLATFORM=offscreen -v "$(pwd)/tests/e2e/qgis:/q:ro,z" -v "$(pwd)/$EV:/e2e:ro,z" \
            "$QGIS_IMAGE" python3 /q/check_geotiff_qgis.py /e2e/clip_town_a_2001.tif </dev/null 2>&1 \
            | grep -E '^(PASS|FAIL|RESULT)' > "$EV/qgis-geotiff.txt"
        cat "$EV/qgis-geotiff.txt"; grep -q '^RESULT OK' "$EV/qgis-geotiff.txt"; check qgis_geotiff $?
    fi

    if has_flag --browser "$@"; then
        step "browser: drive the diagnostic viewer in headless Chromium (needs internet for the MapLibre CDN)"
        mkdir -p "$EV/browser"
        net=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$($C ps -q gateway)")
        docker run --rm --network "$net" --ipc=host -v "$(pwd)/tests/e2e/browser:/check:ro,z" -v "$(pwd)/$EV/browser:/out:z" \
            mcr.microsoft.com/playwright/python:v1.49.1-noble@sha256:42e251ec3238672c931be6c3565ff279690ae072c3e22058128d0412494f6866 \
            sh -c 'pip install -q playwright==1.49.1 >/dev/null 2>&1; python /check/check_viewer.py http://gateway:8080/viewer/ /out' \
            </dev/null > "$EV/browser/check.txt" 2>&1
        rc=$?; grep -E '^(PASS|FAIL|RESULT)' "$EV/browser/check.txt"; check browser_viewer $rc
    fi

    step "restart: recreate every container (volumes kept) and wait for health"
    $C down </dev/null > "$EV/restart.log" 2>&1 && $C up -d --wait </dev/null >> "$EV/restart.log" 2>&1
    check restart $?

    step "pytest: persistence after restart"
    $C run --rm -T e2e -m persistence </dev/null > "$EV/pytest-persistence.txt" 2>&1
    rc=$?; tail -5 "$EV/pytest-persistence.txt"; check pytest_persistence $rc

    step "status, logs and secret scan"
    $C ps -a --format '{{.Service}} {{.State}} {{.Status}}' > "$EV/compose-health.txt" 2>&1
    cat "$EV/compose-health.txt"
    $C logs --no-color --timestamps > "$EV/compose-logs.txt" 2>&1
    leaks=0
    for var in POSTGRES_PASSWORD INGEST_DB_PASSWORD OGC_DB_PASSWORD PROCESSING_DB_PASSWORD; do
        value=$(grep -E "^$var=" .env | head -1 | cut -d= -f2-)
        [ -n "$value" ] || continue
        if grep -R -F -q -- "$value" "$EV" 2>/dev/null || git grep -F -q -- "$value" 2>/dev/null; then
            echo "LEAK: value of $var found in logs, evidence or tracked files"; leaks=$((leaks + 1))
        fi
    done
    check no_secret_leaks "$leaks"
    # Server faults show up as 5xx in the gateway's JSON access log. (pygeoapi logs 4xx with
    # tracebacks, and the restart makes PostgreSQL log FATAL; neither is a server fault.)
    n5xx=$($C logs --no-color gateway 2>/dev/null | grep -c -E '"status":5[0-9][0-9]' || true)
    check no_server_errors "$n5xx"
    finish
}

# Each module's own pytest suite against a throwaway integrated stack. Used by `run.sh modules`
# (make test-modules). OGC integration tests expect PUBLIC_BASE_URL=http://gateway:8080 and a 5 s refresh.
run_modules() {
    set -a; . ./.env; set +a          # owner credentials for the test DSNs (passed by name, never echoed)
    export PGPASSWORD="$POSTGRES_PASSWORD"
    export COMPOSE_PROJECT_NAME=cgis-modtest PUBLIC_BASE_URL=http://gateway:8080 OGC_REFRESH_INTERVAL=5 GATEWAY_PORT=18088
    C="docker compose -p cgis-modtest"
    NET=cgis-modtest_default
    EV=data/modtest-evidence
    rm -rf "$EV" && mkdir -p "$EV" && : > "$EV/steps.txt"
    pg_env="-e PGHOST=db -e PGPORT=5432 -e PGUSER=$POSTGRES_USER -e PGPASSWORD"

    step "start the stack (project cgis-modtest) and seed"
    $C --profile tools build </dev/null > "$EV/build.log" 2>&1 && $C up -d --wait </dev/null > "$EV/up.log" 2>&1
    check stack_up $?
    $C run --rm -T ingestion seed </dev/null > "$EV/seed.jsonl" 2>&1; check seed $?

    step "database: pytest against the stack's PostgreSQL/PostGIS"
    docker run --rm --network "$NET" $pg_env -v "$(pwd)/database:/src:ro,z" "$PY_IMAGE" \
        sh -c 'cp -r /src /tmp/db && cd /tmp/db && pip install -q --root-user-action=ignore ".[test]" >/dev/null 2>&1 &&
               CGIS_TEST_ADMIN_DSN="postgresql://$PGUSER:$PGPASSWORD@db:5432/postgres" python -m pytest -q -p no:cacheprovider' \
        </dev/null > "$EV/database.txt" 2>&1
    check database $?

    step "ingestion: pytest against a disposable database migrated with database/ migrations"
    $C exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS cgis_ingest_test" -c "CREATE DATABASE cgis_ingest_test"' \
        </dev/null > "$EV/ingestion-db.txt" 2>&1
    $C run --rm -T -e PGDATABASE=cgis_ingest_test migrate </dev/null >> "$EV/ingestion-db.txt" 2>&1
    docker run --rm --network "$NET" $pg_env -e INGESTION_TEST_SCHEMA=existing --entrypoint sh cgis-ingestion:local \
        -c 'INGESTION_TEST_DSN="host=db dbname=cgis_ingest_test user=$PGUSER password=$PGPASSWORD" python -m pytest -q -p no:cacheprovider ingestion/tests' \
        </dev/null > "$EV/ingestion.txt" 2>&1
    check ingestion $?

    step "ogc-services: unit + HTTP integration tests through the gateway, adapter tests in the pygeoapi image"
    docker build -q -t cgis-ogc-tests:dev -f ogc-services/tests/Dockerfile ogc-services </dev/null > "$EV/ogc-build.txt" 2>&1
    # Hostile stub files (symlink escape, disguised VRT) for the path-safety tests, only in this throwaway stack.
    docker run --rm -v cgis-modtest_assets:/data/assets -v "$(pwd)/ogc-services:/work:ro,z" -w /work \
        cgis-ogc-tests:dev python tests/make_hostile_assets.py /data/assets </dev/null > "$EV/ogc-stubs.txt" 2>&1
    docker run --rm --network "$NET" -v "$(pwd)/ogc-services:/work:ro,z" \
        -e CGIS_GATEWAY_URL=http://gateway:8080 -e CGIS_TILES_INTERNAL_URL=http://tiles:8000 \
        -e POSTGRES_HOST=db -e POSTGRES_PORT=5432 -e POSTGRES_DB -e POSTGRES_USER -e POSTGRES_PASSWORD \
        cgis-ogc-tests:dev python -m pytest -q -p no:cacheprovider tests/unit tests/integration \
        </dev/null > "$EV/ogc.txt" 2>&1
    check ogc_http $?
    docker run --rm -v "$(pwd)/ogc-services/tests:/cgis/tests:ro,z" -e PYTHONDONTWRITEBYTECODE=1 \
        --entrypoint /venv/bin/python cgis-ogc:local -m pytest -q -p no:cacheprovider /cgis/tests/unit_pygeoapi \
        </dev/null > "$EV/ogc-pygeoapi.txt" 2>&1
    check ogc_pygeoapi_unit $?

    step "spatial-processing: standalone suite (Docker test stage) + PostGIS tests against the stack"
    docker build -q --target test -t cgis-processing-test:local spatial-processing </dev/null > "$EV/processing-build.txt" 2>&1
    check processing_test_stage $?
    docker run --rm --network "$NET" $pg_env -e PGDATABASE="$POSTGRES_DB" --entrypoint sh cgis-processing-test:local \
        -c 'TEST_DATABASE_URL="postgresql://$PGUSER:$PGPASSWORD@db:5432/$PGDATABASE" python -m pytest -q -p no:cacheprovider -m postgis tests' \
        </dev/null > "$EV/processing-postgis.txt" 2>&1
    check processing_postgis $?

    if has_flag --qgis "$@"; then
        step "headless QGIS: OGC API Features + XYZ tiles"
        docker run --rm --network "$NET" -e QT_QPA_PLATFORM=offscreen -v "$(pwd)/ogc-services/tests/qgis:/qgis:ro,z" \
            "$QGIS_IMAGE" python3 /qgis/check_qgis.py </dev/null 2>&1 | grep -vE "^(QStandardPaths|Warning: |qt\.|Application path)" > "$EV/qgis.txt"
        grep -q '^RESULT OK' "$EV/qgis.txt"; check qgis_headless $?
    fi

    if has_flag --cite "$@"; then
        step "OGC CITE (TEAM Engine) Features 1.0 + Tiles 1.0 against the gateway"
        CGIS_TEST_NETWORK="$NET" CGIS_CITE_IUT=http://gateway:8080/ogc/ bash ogc-services/tests/cite/run_cite.sh "$(pwd)/$EV/cite" \
            </dev/null > "$EV/cite.txt" 2>&1
        check ogc_cite $?
    fi

    for f in "$EV"/*.txt; do
        result=$(grep -E '[0-9]+ (passed|failed)' "$f" | tail -1)
        [ -n "$result" ] && printf '%-24s %s\n' "$(basename "$f")" "$result"
    done
    finish
}

case "$MODE" in
    e2e) run_e2e "$@" ;;
    modules) run_modules "$@" ;;
    *) echo "usage: sh tests/run.sh e2e [--qgis] [--browser] | modules [--qgis] [--cite]"; exit 2 ;;
esac
