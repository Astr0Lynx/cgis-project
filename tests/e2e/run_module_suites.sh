#!/bin/sh
# Re-run each module owner's own test suite against the INTEGRATED stack (real Agent 2 schema,
# real Agent 3 fixtures and importer), in a separate Compose project so the main stack is untouched.
#
#   sh tests/e2e/run_module_suites.sh [--cite] [--qgis]   # optional: OGC CITE suites, headless QGIS
#
# Uses project "cgis-modtest" on gateway port 18088 with PUBLIC_BASE_URL=http://gateway:8080 and
# OGC_REFRESH_INTERVAL=5, which Agent 4's integration tests expect (ogc-services/README.md).
# Evidence: ./data/modtest-evidence/ (git-ignored). Tear down: docker compose -p cgis-modtest down -v
set -u
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1
[ -f .env ] || { echo "missing .env"; exit 2; }
set -a; . ./.env; set +a          # owner credentials for the test DSNs (never echoed)
export PGPASSWORD="$POSTGRES_PASSWORD"   # passed to test containers by name (-e PGPASSWORD), not by value

export COMPOSE_PROJECT_NAME=cgis-modtest PUBLIC_BASE_URL=http://gateway:8080 OGC_REFRESH_INTERVAL=5 GATEWAY_PORT=18088
C="docker compose -p cgis-modtest"
NET=cgis-modtest_default
EV=data/modtest-evidence
FAILED=""
rm -rf "$EV" && mkdir -p "$EV"
step() { printf '\n== [%s] %s\n' "$(date +%T)" "$*"; }
check() { if [ "$2" -eq 0 ]; then echo "SUITE PASS  $1"; else echo "SUITE FAIL  $1 (exit $2)"; FAILED="$FAILED $1"; fi
          echo "$1 $2" >> "$EV/suites.txt"; }
pg_env="-e PGHOST=db -e PGPORT=5432 -e PGUSER=$POSTGRES_USER -e PGPASSWORD"   # value taken from the environment

step "start the integrated stack (project cgis-modtest) and seed"
$C --profile tools build </dev/null > "$EV/build.log" 2>&1 && $C up -d --wait </dev/null > "$EV/up.log" 2>&1; check stack_up $?
$C run --rm -T ingestion seed </dev/null > "$EV/seed.jsonl" 2>&1; check seed $?

step "Agent 2 (database): pytest against PostgreSQL 17 / PostGIS 3.5 in the stack"
docker run --rm --network "$NET" $pg_env -v "$(pwd)/database:/src:ro" python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c \
    sh -c 'cp -r /src /tmp/db && cd /tmp/db && pip install -q --root-user-action=ignore ".[test]" >/dev/null 2>&1 &&
           CGIS_TEST_ADMIN_DSN="postgresql://$PGUSER:$PGPASSWORD@db:5432/postgres" python -m pytest -q -p no:cacheprovider' \
    </dev/null > "$EV/agent2-database.txt" 2>&1
check agent2_database $?

step "Agent 3 (ingestion): pytest against a disposable DB migrated with Agent 2's migrations"
$C exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS cgis_ingest_test" -c "CREATE DATABASE cgis_ingest_test"' \
    </dev/null > "$EV/agent3-db-create.txt" 2>&1
$C run --rm -T -e PGDATABASE=cgis_ingest_test migrate </dev/null > "$EV/agent3-migrate.txt" 2>&1
docker run --rm --network "$NET" $pg_env -e INGESTION_TEST_SCHEMA=existing --entrypoint sh cgis-ingestion:local \
    -c 'INGESTION_TEST_DSN="host=db dbname=cgis_ingest_test user=$PGUSER password=$PGPASSWORD" python -m pytest -q -p no:cacheprovider ingestion/tests' \
    </dev/null > "$EV/agent3-ingestion.txt" 2>&1
check agent3_ingestion $?

step "Agent 4 (OGC): unit + HTTP integration tests through the integrated gateway"
docker build -q -t cgis-ogc-tests:dev -f ogc-services/tests/Dockerfile ogc-services </dev/null > "$EV/agent4-build.txt" 2>&1
# Agent 4's path-safety tests expect its hostile stub files (a symlink escaping the asset root and a
# VRT disguised as .tif) under hostile/ in the asset volume; they live apart from the importer's
# rasters/ and vectors/ layout and exist only in this throwaway test stack.
docker run --rm -v cgis-modtest_assets:/data/assets -v "$(pwd)/ogc-services:/work:ro" -w /work     cgis-ogc-tests:dev python tests/stack/make_stub_rasters.py /data/assets </dev/null > "$EV/agent4-stub-assets.txt" 2>&1
docker run --rm --network "$NET" -v "$(pwd)/ogc-services:/work:ro" \
    -e CGIS_GATEWAY_URL=http://gateway:8080 -e CGIS_TILES_INTERNAL_URL=http://tiles:8000 \
    -e POSTGRES_HOST=db -e POSTGRES_PORT=5432 -e POSTGRES_DB -e POSTGRES_USER -e POSTGRES_PASSWORD \
    cgis-ogc-tests:dev python -m pytest -q -p no:cacheprovider tests/unit tests/integration \
    </dev/null > "$EV/agent4-ogc.txt" 2>&1
check agent4_ogc_http $?
docker run --rm -v "$(pwd)/ogc-services/tests:/cgis/tests:ro" -e PYTHONDONTWRITEBYTECODE=1     --entrypoint /venv/bin/python cgis-ogc:local -m pytest -q -p no:cacheprovider /cgis/tests/unit_pygeoapi     </dev/null > "$EV/agent4-ogc-pygeoapi.txt" 2>&1
check agent4_ogc_pygeoapi_unit $?

step "Agent 5 (processing): standalone suite (Docker test stage) + PostGIS tests against the stack"
docker build -q --target test -t cgis-processing-test:local spatial-processing </dev/null > "$EV/agent5-build-test-stage.txt" 2>&1
check agent5_processing_test_stage $?
docker run --rm --network "$NET" $pg_env -e PGDATABASE="$POSTGRES_DB" --entrypoint sh cgis-processing-test:local \
    -c 'TEST_DATABASE_URL="postgresql://$PGUSER:$PGPASSWORD@db:5432/$PGDATABASE" python -m pytest -q -p no:cacheprovider -m postgis tests' \
    </dev/null > "$EV/agent5-processing-postgis.txt" 2>&1
check agent5_processing_postgis $?

case " $* " in *" --qgis "*)
    step "headless QGIS 3.44 interoperability check (Agent 4's tests/qgis/check_qgis.py)"
    docker pull -q qgis/qgis:3.44.15@sha256:9c4afddd8eb4d03275e161b732d57ea402a1a5237eb5766957427530fd38afe0 >/dev/null 2>&1
    docker run --rm --network "$NET" -e QT_QPA_PLATFORM=offscreen -v "$(pwd)/ogc-services/tests/qgis:/qgis:ro"         qgis/qgis:3.44.15@sha256:9c4afddd8eb4d03275e161b732d57ea402a1a5237eb5766957427530fd38afe0         python3 /qgis/check_qgis.py </dev/null 2>&1 | grep -vE "^(QStandardPaths|Warning: |qt\.|Application path)" > "$EV/qgis.txt"
    grep -q '^RESULT OK' "$EV/qgis.txt"
    check qgis_headless $?
    ;;
esac

case " $* " in *" --cite "*)
    step "OGC CITE (TEAM Engine) Features 1.0 + Tiles 1.0 against the integrated gateway"
    CGIS_TEST_NETWORK="$NET" CGIS_CITE_IUT=http://gateway:8080/ogc/ bash ogc-services/tests/cite/run_cite.sh "$(pwd)/$EV/cite" \
        </dev/null > "$EV/cite.txt" 2>&1
    check ogc_cite $?
    ;;
esac

step "summary"
for f in "$EV"/agent*.txt; do printf '%-40s %s\n' "$(basename "$f")" "$(grep -E '[0-9]+ (passed|failed)' "$f" | tail -1)"; done
cat "$EV/suites.txt"
[ -z "$FAILED" ] && { echo "MODULE SUITES: PASS"; exit 0; }
echo "MODULE SUITES: FAIL ->$FAILED"; exit 1
