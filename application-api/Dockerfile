# syntax=docker/dockerfile:1
# CyberGIS application API. Build context: the REPOSITORY ROOT, because the images reuse the
# existing ingestion/ importer and the spatial-processing/ clip code unchanged (see Dockerfile.dockerignore).
#
#   docker build -f application-api/Dockerfile --target api           -t cgis-app-api:local .
#   docker build -f application-api/Dockerfile --target ingest-worker -t cgis-app-ingest-worker:local .
#   docker build -f application-api/Dockerfile --target test .        # unit tests (no database)
#
# Targets:
#   api            HTTP service + export worker (fastapi/rasterio pins of spatial-processing/pyproject.toml)
#   ingest-worker  validate/publish worker on the INGESTION image's own pins (ingestion/requirements.txt)
# Neither image contains Docker tooling or talks to a Docker socket.
FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_STAGING_ROOT=/data/staging \
    APP_ASSET_ROOT=/data/assets \
    APP_EXPORT_ROOT=/data/exports
# The GDAL bundled in the rasterio wheels needs libexpat, which slim images lack (same fix as ingestion/).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data/staging /data/exports /data/assets \
    && chown app:app /data/staging /data/exports
WORKDIR /app

# ------------------------------------------------------------------ api (and export worker)
FROM base AS api
COPY spatial-processing/pyproject.toml /opt/src/spatial-processing/pyproject.toml
COPY spatial-processing/src /opt/src/spatial-processing/src
RUN pip install /opt/src/spatial-processing
COPY application-api/pyproject.toml /opt/src/application-api/pyproject.toml
COPY application-api/src /opt/src/application-api/src
RUN pip install /opt/src/application-api && rm -rf /opt/src
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/app/ready', timeout=4).status == 200 else 1)"]
CMD ["cgis-app", "serve", "--host", "0.0.0.0", "--port", "8000"]

# ------------------------------------------------------------------ unit tests (no database needed)
FROM api AS test
USER root
COPY application-api/pyproject.toml /opt/src/application-api/pyproject.toml
COPY application-api/tests /opt/src/application-api/tests
RUN pip install "pytest==9.2.0" "httpx==0.28.1"
USER app
WORKDIR /opt/src/application-api
RUN python -m pytest -q -p no:cacheprovider tests/test_unit.py

# ------------------------------------------------------------------ ingest worker (existing importer)
FROM base AS ingest-worker
COPY ingestion/requirements.txt /opt/src/ingestion-requirements.txt
RUN pip install -r /opt/src/ingestion-requirements.txt && rm -rf /opt/src
# The importer is used as a library, exactly as committed in ingestion/ (not modified here).
COPY ingestion/ /app/ingestion/
COPY application-api/src/cgis_app /app/cgis_app
ENV PYTHONPATH=/app
USER app
CMD ["python", "-m", "cgis_app", "worker", "--queue", "ingest"]
