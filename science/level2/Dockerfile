# Level 2 image (Python 3.12, GDAL 3.9.3 via rasterio wheels). Build from the repository root, e.g.
#   git archive --format=tar HEAD | docker build -f science/level2/Dockerfile --build-arg MODEL_COMMIT="$(git rev-parse HEAD)" -t cgis-level2 -
#   docker run --rm --memory=6g -v "$PWD/data:/repo/data" cgis-level2 run --config data/incoming/level2/config.json
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /repo
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY ingestion/requirements.txt ingestion/requirements.txt
COPY science/level2/requirements.txt science/level2/requirements.txt
RUN pip install --no-cache-dir -r science/level2/requirements.txt \
    && python -c "import fiona, rasterio"
COPY ingestion ingestion
COPY science/level2 science/level2
ARG MODEL_COMMIT
ENV CGIS_LEVEL2_BUILD_COMMIT=${MODEL_COMMIT}
ENTRYPOINT ["python", "-m", "science.level2.cgis_level2"]
