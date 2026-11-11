# Headless Level 1 science image. Build from the REPOSITORY ROOT so the contract tests can import ingestion/:
#   docker build -f science/level1/Dockerfile -t cgis-level1 .
#   docker run --rm --memory=16g -v "$PWD/data:/repo/data" cgis-level1 run --config science/level1/config/india_full.toml
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/repo/science/level1:/repo \
    GDAL_CACHEMAX=256 MPLBACKEND=Agg
WORKDIR /repo
# rasterio 1.5 manylinux wheels link against the system libexpat
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
COPY science/level1/requirements.txt science/level1/requirements-dev.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
COPY ingestion /repo/ingestion
COPY science/level1 /repo/science/level1
RUN useradd -m -u 1000 science && mkdir -p /repo/data && chown science /repo/data
USER science
ENTRYPOINT ["python", "-m", "cgis_level1"]
CMD ["--help"]
