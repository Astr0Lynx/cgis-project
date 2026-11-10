#!/usr/bin/env bash
# Run a command against a throwaway local PostGIS cluster (unix socket only, no TCP listener).
# Exports APP_TEST_DSN (a superuser on a fresh database) for the command, then deletes the cluster.
#
#   application-api/scripts/with_temp_postgis.sh python -m pytest application-api/tests
#
# Needs PostgreSQL >= 14 server binaries (initdb, pg_ctl) and the PostGIS extension on this host
# (e.g. a conda env: conda create -c conda-forge postgresql=17 postgis=3.5). Set PG_BIN to their directory.
# Inside Compose, point APP_TEST_DSN at a disposable database instead.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

PG_BIN="${PG_BIN:-$(dirname "$(command -v pg_ctl)")}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cgis-app-pg.XXXXXX")"
PORT="${PGTEST_PORT:-55433}"

cleanup() {
  "$PG_BIN/pg_ctl" -D "$WORK/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

"$PG_BIN/initdb" -D "$WORK/data" -U app_test_owner -A trust --no-sync >/dev/null
# Unix socket paths are limited to 107 bytes; with a long TMPDIR fall back to a loopback-only TCP listener.
if [ "${#WORK}" -lt 90 ]; then
  HOST="$WORK"; LISTEN="''"; SOCKDIR="$WORK"
else
  HOST="127.0.0.1"; LISTEN="'127.0.0.1'"; SOCKDIR="''"
fi
"$PG_BIN/pg_ctl" -D "$WORK/data" -l "$WORK/pg.log" -w \
  -o "-c listen_addresses=$LISTEN -c unix_socket_directories=$SOCKDIR -p $PORT -c fsync=off -c max_connections=200" start >/dev/null || {
  tail -5 "$WORK/pg.log" >&2; exit 1; }
"$PG_BIN/createdb" -h "$HOST" -p "$PORT" -U app_test_owner cgis_app_test

export APP_TEST_DSN="host=$HOST port=$PORT user=app_test_owner dbname=cgis_app_test"
"$@"
