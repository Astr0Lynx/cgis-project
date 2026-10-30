# Ingestion service image. Build context: the ingestion/ directory.
#   docker build -t cgis-ingestion ingestion/
# Compose wiring is requested in ingestion/docs/compose-request.md (owned by Agent 1).
FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INGESTION_ASSETS_DIR=/data/assets \
    INGESTION_FIXTURES_DIR=/data/fixtures

WORKDIR /app
# libexpat1 is required by the GDAL bundled in the rasterio wheel and is absent from slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt /app/ingestion/
RUN pip install -r /app/ingestion/requirements-dev.txt \
    && useradd --create-home --uid 10001 ingest \
    && mkdir -p /data/assets /data/fixtures \
    && chown -R ingest:ingest /data
COPY . /app/ingestion/

USER ingest
ENTRYPOINT ["python", "-m", "ingestion.cli"]
CMD ["--help"]
