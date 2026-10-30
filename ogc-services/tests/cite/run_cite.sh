#!/usr/bin/env bash
# Run the OGC CITE executable test suites (TEAM Engine) against the test stack.
#
#   docker compose -f ogc-services/tests/stack/compose.yaml up -d --build --wait
#   ogc-services/tests/cite/run_cite.sh [output-dir]
#
# Features: ets-ogcapi-features10 1.8 (Part 1 Core + Part 2 CRS).
# Tiles:    ets-ogcapi-tiles10 1.1 (Core, tileset, tilesets-list, geodata-tilesets checks).
# Tile test inputs are read from the server's own tileset metadata, not hand-picked.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/results}"
NETWORK="${CGIS_TEST_NETWORK:-cgis-ogc-test_default}"
IUT="${CGIS_CITE_IUT:-http://gateway:8080/ogc/}"
COLLECTION="${CGIS_CITE_RASTER:-synthetic-worldpop-2001}"
FEATURES_IMAGE="ogccite/ets-ogcapi-features10:1.8-teamengine-5.7@sha256:d884de89ac8dae6ea153766886c706f9ddf6ce172de0c4bd4bdd591d28bdaffe"
TILES_IMAGE="ogccite/ets-ogcapi-tiles10:1.1-teamengine-5.7@sha256:ceab021daa7a1054403e3c175c6553a96cbf38bafb9021aa7246b5dd39dd0b1b"
RUNNER_IMAGE="${CGIS_TEST_IMAGE:-cgis-ogc-tests:dev}"
mkdir -p "$OUT"

cleanup() { docker rm -f cgis-cite-features cgis-cite-tiles >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker run -d --name cgis-cite-features --network "$NETWORK" "$FEATURES_IMAGE" >/dev/null
docker run -d --name cgis-cite-tiles --network "$NETWORK" "$TILES_IMAGE" >/dev/null

wait_ready() {
  for _ in $(seq 1 100); do
    if docker exec "$1" curl -sf -o /dev/null -u ogctest:ogctest http://localhost:8080/teamengine/rest/suites; then return 0; fi
    sleep 3
  done
  echo "TEAM Engine in $1 did not start" >&2; return 1
}

run_suite() {  # container suite query outfile
  docker exec "$1" curl -sf -u ogctest:ogctest -H "Accept: application/xml" \
    "http://localhost:8080/teamengine/rest/suites/$2/run?$3" > "$4"
}

wait_ready cgis-cite-features
wait_ready cgis-cite-tiles

FEATURES_QUERY="iut=$(python3 -c 'import sys,urllib.parse as u; print(u.quote(sys.argv[1], safe=""))' "$IUT")&noofcollections=-1"
run_suite cgis-cite-features ogcapi-features-1.0 "$FEATURES_QUERY" "$OUT/cite-ogcapi-features-1.0.xml"

TILES_QUERY="$(docker run --rm --network "$NETWORK" -e IUT="$IUT" -e COLLECTION="$COLLECTION" "$RUNNER_IMAGE" python -c '
import os, httpx, urllib.parse as u
iut, coll = os.environ["IUT"].rstrip("/"), os.environ["COLLECTION"]
md = httpx.get(f"{iut}/collections/{coll}/tiles/WebMercatorQuad?f=json").raise_for_status().json()
limit = next(l for l in md["tileMatrixSetLimits"] if l["tileMatrix"] == "11")
item = next(l["href"] for l in md["links"] if l["rel"] == "item")
q = {"iut": iut + "/", "noofcollections": "-1", "tilematrixsetdefinitionuri": md["tileMatrixSetURI"],
     "urltemplatefortiles": item, "tilematrix": limit["tileMatrix"],
     "mintilerow": limit["minTileRow"], "maxtilerow": limit["maxTileRow"],
     "mintilecol": limit["minTileCol"], "maxtilecol": limit["maxTileCol"]}
print(u.urlencode(q, quote_via=u.quote, safe=""))')"
run_suite cgis-cite-tiles ogcapi-tiles-1.0 "$TILES_QUERY" "$OUT/cite-ogcapi-tiles-1.0.xml"

docker run --rm -v "$OUT:/out:ro" -v "$HERE:/cite:ro" "$RUNNER_IMAGE" \
  python /cite/summarize.py /out/cite-ogcapi-features-1.0.xml /out/cite-ogcapi-tiles-1.0.xml
