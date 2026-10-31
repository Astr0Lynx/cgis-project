#!/bin/sh
# Assertions for the mock gateway stack (infra/tests/compose.mock.yaml). Needs curl.
set -u
BASE="${1:-http://127.0.0.1:${CGIS_TEST_GATEWAY_PORT:-18080}}"
BIG=$(mktemp)
trap 'rm -f "$BIG"' EXIT
head -c 1100000 /dev/zero > "$BIG"   # a file, not a pipe: SIGPIPE handling differs on Windows shells
fails=0
expect() {  # expect <description> <actual> <expected substring>
    case "$2" in
        *"$3"*) echo "PASS  $1" ;;
        *) echo "FAIL  $1: got [$2], want [$3]"; fails=$((fails + 1)) ;;
    esac
}
get() { curl -s --max-time 10 "$@"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }
hdr() { curl -s -o /dev/null -D - --max-time 10 "$@" | tr -d '\r' | grep -i "^$2:" | cut -d' ' -f2-; }

expect "/ogc/ prefix stripped, query kept" "$(get "$BASE/ogc/collections/x/items?bbox=1,2,3,4&f=json")" '"service": "ogc", "method": "GET", "path": "/collections/x/items?bbox=1,2,3,4&f=json"'
expect "/ogc/ forwards client Host"        "$(get "$BASE/ogc/")" "\"host\": \"${BASE#http://}\""
expect "/ogc/ percent-encoding preserved"  "$(get "$BASE/ogc/collections/a%2Fb/items")" '"path": "/collections/a%2Fb/items"'
expect "/tiles/ prefix stripped"           "$(get "$BASE/tiles/datasets/d/WebMercatorQuad/tilejson.json")" '"service": "tiles", "method": "GET", "path": "/datasets/d/WebMercatorQuad/tilejson.json"'
expect "/api/ prefix stripped, POST ok"    "$(get -X POST -H 'content-type: application/json' -d '{}' "$BASE/api/operations/clip")" '"service": "processing", "method": "POST", "path": "/operations/clip"'
expect "POST to /ogc/ refused"             "$(code -X POST -d x "$BASE/ogc/collections")" "403"
expect "DELETE to /tiles/ refused"         "$(code -X DELETE "$BASE/tiles/datasets")" "403"
expect "HEAD to /ogc/ allowed"             "$(code -I "$BASE/ogc/")" "200"
expect "/api/ body over 1 MiB refused"     "$(code -X POST --data-binary @"$BIG" "$BASE/api/operations/clip")" "413"
expect "/ogc redirects to /ogc/ (same host:port)" "$(hdr "$BASE/ogc" location)" "$BASE/ogc/"
expect "/tiles redirects to /tiles/"       "$(hdr "$BASE/tiles" location)" "$BASE/tiles/"
expect "/ redirects to /viewer/"           "$(hdr "$BASE/" location)" "$BASE/viewer/"
expect "/viewer/ served by viewer"         "$(code "$BASE/viewer/healthz")" "200"
expect "raw dot-segment refused"          "$(code --path-as-is "$BASE/ogc/../tiles/x")" "400"
expect "encoded dot-segment refused"      "$(code --path-as-is "$BASE/tiles/%2e%2e/api/x")" "400"
expect "/viewer/ path stripped"           "$(code "$BASE/viewer/")" "200"
expect "/healthz ok"                       "$(get "$BASE/healthz")" '"status":"ok"'
expect "unknown route is JSON 404"         "$(get "$BASE/nope")" '"code":"NOT_FOUND"'
expect "no nginx version banner"           "$(hdr "$BASE/healthz" server)" "nginx"
case "$(hdr "$BASE/healthz" server)" in *nginx/*) echo "FAIL  server header leaks version"; fails=$((fails+1));; esac

[ "$fails" -eq 0 ] && echo "gateway routing: all checks passed" || echo "gateway routing: $fails failure(s)"
[ "$fails" -eq 0 ]
