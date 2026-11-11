#!/usr/bin/env bash
# Run the application-api test suite against a throwaway PostGIS cluster.
#
#   PYTHON=/path/to/api-venv/bin/python \
#   APP_TEST_INGEST_PYTHON=/path/to/ingest-venv/bin/python \
#   PG_BIN=/path/to/postgres/bin \
#   application-api/scripts/run_tests.sh [pytest args...]
#
# PYTHON: interpreter with application-api/pyproject.toml [test] deps + database/ + spatial-processing/.
# APP_TEST_INGEST_PYTHON (optional, recommended): interpreter with ingestion/requirements.txt, used to run the
#   ingest worker out-of-process exactly like the ingest-worker image. Unset = in-process.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$HERE")"
PYTHON="${PYTHON:-python3}"
cd "$APP"
# The rasterio/pyproj wheels bundle their own PROJ/GDAL data; a host conda/OSGeo install must not override it.
unset PROJ_LIB PROJ_DATA GDAL_DATA GDAL_DRIVER_PATH
exec "$HERE/with_temp_postgis.sh" "$PYTHON" -m pytest -p no:cacheprovider "$@"
