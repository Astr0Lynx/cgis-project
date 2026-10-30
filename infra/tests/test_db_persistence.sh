#!/bin/sh
# DB readiness + persistence across `down`/`up` without -v. Uses its own Compose project name.
# Usage: sh infra/tests/test_db_persistence.sh   (run from the repository root; needs .env)
set -eu
P="docker compose -p cgis-persist-test -f compose.yaml"
psql_db() { $P exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -X -q -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }
cleanup() { $P down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

$P up -d --wait db
psql_db <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS public.persist_probe (v text);
INSERT INTO public.persist_probe VALUES ('survives-restart');
SQL
$P down            # keeps volumes
$P up -d --wait db
got=$(printf "SELECT v FROM public.persist_probe;\n" | psql_db)
ver=$(printf "SELECT current_setting('server_version') || ' / PostGIS ' || postgis_lib_version();\n" | psql_db)
echo "server: $ver"
if [ "$got" = "survives-restart" ]; then echo "PASS  row survived down/up without -v"; else echo "FAIL  got [$got]"; exit 1; fi
