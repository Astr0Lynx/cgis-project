#!/usr/bin/env bash
# Run a command against a throwaway local PostGIS cluster (unix socket only, no TCP, no password).
# Exports INGESTION_TEST_DSN for the command, then stops and deletes the cluster.
#
#   ingestion/scripts/with_temp_postgis.sh python -m pytest ingestion/tests
#
# Requires PostgreSQL server binaries (initdb, pg_ctl) and the PostGIS extension on this host.
# Inside Compose, point INGESTION_TEST_DSN at a disposable database instead of using this script.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

PG_BIN="${PG_BIN:-$(dirname "$(command -v pg_ctl)")}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ingestion-pg.XXXXXX")"
PORT="${PGTEST_PORT:-55432}"

cleanup() {
  "$PG_BIN/pg_ctl" -D "$WORK/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

"$PG_BIN/initdb" -D "$WORK/data" -U ingest_test -A trust --no-sync >/dev/null
"$PG_BIN/pg_ctl" -D "$WORK/data" -l "$WORK/pg.log" -w \
  -o "-c listen_addresses='' -k $WORK -p $PORT -c fsync=off" start >/dev/null
"$PG_BIN/createdb" -h "$WORK" -p "$PORT" -U ingest_test ingest_test

export INGESTION_TEST_DSN="host=$WORK port=$PORT user=ingest_test dbname=ingest_test"
"$@"
