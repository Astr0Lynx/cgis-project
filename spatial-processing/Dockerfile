# CyberGIS Phase 2 — spatial processing service (Agent 5).
# rasterio wheels bundle GDAL/PROJ, so no system GDAL packages are needed.
FROM python:3.12.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . \
    && useradd --system --uid 10001 --home-dir /app processing \
    && mkdir -p /data/outputs \
    && chown processing /data/outputs

# --- test stage: `docker build --target test spatial-processing/` runs the standalone suite
FROM base AS test
COPY tests ./tests
RUN pip install ".[test]" && python -m pytest -q

# --- runtime
FROM base AS runtime
USER processing
ENV ASSET_ROOT=/data/assets \
    OUTPUT_ROOT=/data/outputs \
    ROOT_PATH=/api \
    CATALOG_BACKEND=postgres \
    JOB_STORE_BACKEND=postgres
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "--factory", "spatial_processing.app:app_factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
