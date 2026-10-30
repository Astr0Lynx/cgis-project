#!/bin/bash
# Publish catalog -> pygeoapi config, generate OpenAPI, then serve with gunicorn.
set -euo pipefail

export PYGEOAPI_CONFIG="${PYGEOAPI_CONFIG:-/cgis/runtime/pygeoapi-config.yml}"
export PYGEOAPI_OPENAPI="${PYGEOAPI_OPENAPI:-/cgis/runtime/pygeoapi-openapi.yml}"
export CGIS_OGC_PIDFILE="${CGIS_OGC_PIDFILE:-/cgis/runtime/gunicorn.pid}"

case "${1:-serve}" in
  serve)
    /venv/bin/python -m cgis_ogc.publish --wait "${CGIS_OGC_DB_WAIT:-120}"
    if [ "${OGC_REFRESH_INTERVAL:-0}" -gt 0 ]; then
      echo "catalog refresh every ${OGC_REFRESH_INTERVAL}s"
      ( while sleep "${OGC_REFRESH_INTERVAL}"; do
          /venv/bin/python -m cgis_ogc.publish --refresh || echo "catalog refresh failed; keeping current config"
        done ) &
    fi
    exec /venv/bin/gunicorn \
      --workers "${WSGI_WORKERS:-4}" \
      --worker-class "${WSGI_WORKER_CLASS:-gevent}" \
      --timeout "${WSGI_WORKER_TIMEOUT:-60}" \
      --bind "0.0.0.0:${CONTAINER_PORT:-5000}" \
      --pid "${CGIS_OGC_PIDFILE}" \
      --access-logfile - \
      cgis_ogc.wsgi:APP
    ;;
  publish|refresh)
    shift
    exec /venv/bin/python -m cgis_ogc.publish --refresh "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
