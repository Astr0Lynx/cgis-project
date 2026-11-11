#!/usr/bin/env bash
# Run the application-api test suite against a throwaway PostGIS cluster (unix socket, deleted on exit).
#   PG_BIN=<postgres bin> PYTHON=<api venv python> APP_TEST_INGEST_PYTHON=<ingestion venv python> \
#   APP_TEST_NGINX=<nginx> application-api/scripts/run_tests.sh [pytest args...]
# PYTHON needs application-api[test] + database/ + spatial-processing/; APP_TEST_INGEST_PYTHON (optional)
# runs the ingest worker out of process on ingestion/requirements.txt, like the ingest-worker image.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
PG_BIN="${PG_BIN:-$(dirname "$(command -v pg_ctl)")}"
PORT="${PGTEST_PORT:-55433}"
# Unix socket paths must stay under 107 bytes, so the cluster lives in a short temp dir.
WORK="$(mktemp -d /tmp/cgis-app-pg.XXXXXX)"
trap '"$PG_BIN/pg_ctl" -D "$WORK/data" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT

"$PG_BIN/initdb" -D "$WORK/data" -U app_test_owner -A trust --no-sync >/dev/null
"$PG_BIN/pg_ctl" -D "$WORK/data" -l "$WORK/pg.log" -w -o "-c listen_addresses='' -c unix_socket_directories=$WORK \
  -p $PORT -c fsync=off -c max_connections=200" start >/dev/null || { tail -5 "$WORK/pg.log" >&2; exit 1; }
"$PG_BIN/createdb" -h "$WORK" -p "$PORT" -U app_test_owner cgis_app_test
export APP_TEST_DSN="host=$WORK port=$PORT user=app_test_owner dbname=cgis_app_test"

cd "$APP"
# The rasterio/pyproj wheels bundle their own PROJ/GDAL data; a host conda/OSGeo install must not override it.
unset PROJ_LIB PROJ_DATA GDAL_DATA GDAL_DRIVER_PATH
"$PYTHON" -m pytest -p no:cacheprovider "$@"
