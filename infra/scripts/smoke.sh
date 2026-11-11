#!/bin/sh
# Route-level smoke test through the gateway: every public prefix reaches a ready upstream.
# Usage: sh infra/scripts/smoke.sh [BASE_URL]   (default http://localhost:8080; needs curl)
# This checks wiring only; functional tests: sh tests/run.sh e2e.
set -u
BASE="${1:-${PUBLIC_BASE_URL:-http://localhost:8080}}"
fails=0
# A real file instead of /dev/null: native Windows curl.exe cannot write to /dev/null (exit 23).
SINK=$(mktemp)
trap 'rm -f "$SINK"' EXIT

# Request a path and compare the HTTP status: check <expected> <path> <description>. Used below.
check() {
    code=$(curl -s -o "$SINK" -w '%{http_code}' --max-time 15 "$BASE$2")
    [ -n "$code" ] || code=000
    if [ "$code" = "$1" ]; then
        printf 'PASS  %-3s %-34s %s\n' "$code" "$2" "$3"
    else
        printf 'FAIL  %-3s %-34s %s (expected %s)\n' "$code" "$2" "$3" "$1"
        fails=$((fails + 1))
    fi
}

check 200 /healthz                "gateway liveness"
check 302 /                       "root redirects to viewer"
check 200 /viewer/healthz         "viewer (static)"
check 200 "/ogc/conformance?f=json" "OGC API conformance (pygeoapi)"
check 301 /ogc                    "prefix without slash redirects"
check 200 /tiles/readyz           "tiles readiness (TiTiler, vendor routes)"
check 200 /api/ready              "processing readiness"
check 404 /nope                   "unknown route is 404"
check 200 /api/health             "processing liveness (anonymous in both read-access modes)"
case "${COMPOSE_PROFILES:-}" in
    *app*) check 200 /api/app/health "application API (profile app)" ;;
    *)     check 503 /auth/me        "application API not enabled: structured 503" ;;
esac

if [ "$fails" -eq 0 ]; then echo "smoke: all checks passed ($BASE)"; else echo "smoke: $fails check(s) failed ($BASE)"; fi
[ "$fails" -eq 0 ]
