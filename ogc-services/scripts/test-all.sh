#!/usr/bin/env bash
# Build the ogc-services test stack, run every test suite and the OGC CITE suites, tear down.
# Usage: ogc-services/scripts/test-all.sh [--keep]   (--keep leaves the stack running)
set -euo pipefail

MODULE="$(cd "$(dirname "$0")/.." && pwd)"
STACK="$MODULE/tests/stack/compose.yaml"
EVIDENCE="${CGIS_EVIDENCE_DIR:-$MODULE/tests/cite/results}"
KEEP="${1:-}"

compose() { docker compose -f "$STACK" "$@"; }
finish() { [ "$KEEP" = "--keep" ] || compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap finish EXIT

compose up -d --build --wait
compose ps --format '{{.Service}} {{.State}} {{.Health}}'

echo "== unit + integration (external HTTP via gateway stub)"
compose run --rm tests python -m pytest -q -p no:cacheprovider tests/unit tests/integration

echo "== adapter/provider unit tests inside the pygeoapi image"
docker run --rm -v "$MODULE/tests:/cgis/tests:ro" -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint /venv/bin/python cgis-ogc:dev -m pytest -q -p no:cacheprovider /cgis/tests/unit_pygeoapi

echo "== OGC CITE (TEAM Engine)"
"$MODULE/tests/cite/run_cite.sh" "$EVIDENCE"

QGIS_IMAGE="qgis/qgis:3.44.15@sha256:9c4afddd8eb4d03275e161b732d57ea402a1a5237eb5766957427530fd38afe0"
RUN_QGIS="${CGIS_RUN_QGIS:-auto}"   # 1 = always (pulls ~0.9 GB), 0 = never, auto = if image present
if [ "$RUN_QGIS" = 1 ] || { [ "$RUN_QGIS" = auto ] && docker image inspect "$QGIS_IMAGE" >/dev/null 2>&1; }; then
  echo "== headless QGIS interoperability check"
  docker run --rm --network cgis-ogc-test_default -e QT_QPA_PLATFORM=offscreen \
    -v "$MODULE/tests/qgis:/qgis:ro" "$QGIS_IMAGE" python3 /qgis/check_qgis.py 2>&1 \
    | grep -vE "^(QStandardPaths|Warning: |qt\.|Application path)"
else
  echo "== headless QGIS check skipped (set CGIS_RUN_QGIS=1 to pull $QGIS_IMAGE)"
fi
